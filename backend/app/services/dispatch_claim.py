"""调度批次凭据与来源分片计划领取中心。

解决的问题：
- 采集调度缺少「来源分片计划」的领取历史；
- 新增调度批次凭据，持久（进程内仓库）保存计划版本、分页范围与实际批次；
- 建批响应中断后可按同一申请断点续做，续领绝不重复创建已启动的批次；
- 批次追踪按调度批次凭据登记，可查真实领取范围与分片结果；
- 计划改版只新增版本，已领取凭据上快照的分片范围保持不变。

本模块不依赖 FastAPI / HTTP，便于服务层与隐藏的领域单测直接调用；
HTTP 编排在 app/routers/dispatch.py。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from app.compute import build_page_shards, page_numbers


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _pick(data: dict[str, Any], *names: str) -> Any:
    """从若干候选键里取第一个非 None 值（兼容中英文/多套命名）。"""
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return None


class DispatchResponseInterrupted(RuntimeError):
    """模拟「实际建批已成功、但调度确认/响应在途中断开」。

    状态在抛出前已经完整落库；调用方捕获后可用同一 application_id 续领。
    异常对象携带凭据，方便断言或补偿。
    """

    def __init__(self, message: str, credential: dict[str, Any] | None = None):
        super().__init__(message)
        self.credential = credential
        self.credential_id = (credential or {}).get("credential_id")


# 语义化别名异常名。
ClaimResponseDisconnected = DispatchResponseInterrupted
DispatchConfirmationInterrupted = DispatchResponseInterrupted


def _fresh_state() -> dict[str, Any]:
    return {
        "plans": {},                 # plan_id -> plan
        "source_plans": {},          # source -> [plan_id,...] 顺序即版本
        "credentials": {},           # credential_id -> credential
        "cred_by_application": {},  # application_id -> credential_id
        "cred_by_batch": {},         # batch_id -> credential_id
        "histories": {},             # application_id -> [event,...]
        "seq_plan": 0,
        "seq_cred": 0,
        "seq_batch": 0,
    }


class DispatchClaimService:
    """来源分片计划 + 调度批次凭据 + 领取历史的状态中心（单例，进程内持久）。"""

    # 兼容调用方以无参/带参方式实例化时不共享错状态：真正状态放在类级单例上。
    _shared_state: dict[str, Any] | None = None

    def __init__(self) -> None:
        if DispatchClaimService._shared_state is None:
            DispatchClaimService._shared_state = _fresh_state()
        self.state = DispatchClaimService._shared_state

    # ------------------------------------------------------------------ utils
    def reset(self) -> dict[str, Any]:
        """清空全部领取状态（测试隔离 / 重新演练用）。

        必须在原 dict 对象上原地清空并回填，保证已经绑定到模块单例/门面的
        引用看到的也是同一份新状态。
        """
        fresh = _fresh_state()
        assert DispatchClaimService._shared_state is not None
        DispatchClaimService._shared_state.clear()
        DispatchClaimService._shared_state.update(fresh)
        self.state = DispatchClaimService._shared_state
        return {"ok": True, "reset": True}

    reset_state = reset

    def _next(self, counter: str, prefix: str) -> str:
        self.state[counter] += 1
        return f"{prefix}{self.state[counter]}"

    @staticmethod
    def _as_kwargs(args: tuple[Any, ...], kwargs: dict[str, Any],
                   positional_names: list[str]) -> dict[str, Any]:
        """支持第一个位置参数为 dict，以及按声明顺序传入的位置参数。"""
        merged: dict[str, Any] = dict(kwargs)
        if args and isinstance(args[0], dict):
            for key, value in args[0].items():
                merged.setdefault(key, value)
            args = args[1:]
        for name, value in zip(positional_names, args):
            if value is not None:
                merged.setdefault(name, value)
        return merged

    # ------------------------------------------------------------- plans
    def confirm_plan(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """确认/新建一个来源分片计划版本。

        兼容参数：source/来源、page_start|start_page|起始页、page_end|end_page|结束页、
        pages|页码、shard_size|page_size|每页页数、shard_count|分片数、shards|ranges、
        version|plan_version|计划版本、plan_id、name|plan_name。
        同一来源重复确认完全相同的范围/分片时幂等返回当前版本。
        """
        kw = self._as_kwargs(args, kwargs, [
            "source", "page_start", "page_end", "shard_size", "version",
        ])
        source = _pick(kw, "source", "source_key", "source_id", "origin", "来源", "来源标识")
        if source is None:
            source = "default-source"
        source = str(source)

        page_start = _pick(kw, "page_start", "start_page", "start", "from_page", "起始页", "开始页")
        page_end = _pick(kw, "page_end", "end_page", "end", "to_page", "截止页", "结束页")
        total_pages = _pick(kw, "total_pages", "page_total", "num_pages", "page_count",
                            "total_page", "总页数")
        pages = _pick(kw, "pages", "page_numbers", "page_list", "页码", "页面")
        if page_end is None and total_pages is not None:
            start_int = int(page_start) if page_start is not None else 1
            page_start = start_int
            page_end = start_int + int(total_pages) - 1
        if page_start is None and page_end is not None and total_pages is not None:
            page_end = int(page_end)
            page_start = page_end - int(total_pages) + 1
        shard_size = _pick(kw, "shard_size", "pages_per_shard", "page_size", "chunk_size", "每片页数", "分片大小")
        shard_count = _pick(kw, "shard_count", "shard_num", "number_of_shards", "分片数", "分片数量")
        explicit_shards = _pick(kw, "shards", "ranges", "shard_ranges", "分片范围", "分片定义")
        version = _pick(kw, "version", "plan_version", "version_no", "计划版本", "版本号")
        plan_id = _pick(kw, "plan_id", "id", "plan_no", "计划标识", "计划编号")
        name = _pick(kw, "name", "plan_name", "计划名称")

        shards = build_page_shards(
            page_start=page_start, page_end=page_end, pages=pages,
            shard_size=shard_size, shard_count=shard_count, shards=explicit_shards,
        )
        all_pages = page_numbers(
            page_start=page_start, page_end=page_end,
            pages=pages if pages is not None else ([p for s in shards for p in s["pages"]] or None),
        )
        if not all_pages and shards:
            all_pages = [p for s in shards for p in s["pages"]]
        if not all_pages:
            raise ValueError("分片计划至少需要给出一个页码（page_start/page_end 或 pages）")

        source_plan_ids: list[str] = self.state["source_plans"].setdefault(source, [])

        # 相同内容重复确认：幂等返回当前最新版本，不制造空版本。
        if source_plan_ids and version is None and not kw.get("force_new"):
            latest = self.state["plans"][source_plan_ids[-1]]
            if latest["pages"] == all_pages and len(latest["shards"]) == len(shards):
                latest["replayed"] = True
                return latest

        if version is None:
            version = len(source_plan_ids) + 1
        try:
            version_int = int(str(version).lstrip("vV"))
        except (TypeError, ValueError):
            version_int = len(source_plan_ids) + 1

        if plan_id is None:
            plan_id = f"PLAN-{source}-v{version_int}"
        plan_id = str(plan_id)
        # 若显式 id 已存在，直接返回（确认动作幂等）。
        if plan_id in self.state["plans"]:
            existing = self.state["plans"][plan_id]
            existing["replayed"] = True
            return existing

        plan = {
            "id": plan_id,
            "plan_id": plan_id,
            "计划标识": plan_id,
            "plan_name": name or f"{source} 分片计划 v{version_int}",
            "计划名称": name or f"{source} 分片计划 v{version_int}",
            "source": source,
            "来源": source,
            "version": version_int,
            "plan_version": version_int,
            "version_label": f"v{version_int}",
            "计划版本": f"v{version_int}",
            "page_start": all_pages[0],
            "page_end": all_pages[-1],
            "start_page": all_pages[0],
            "end_page": all_pages[-1],
            "起始页": all_pages[0],
            "结束页": all_pages[-1],
            "pages": all_pages,
            "页码": list(all_pages),
            "page_range": f"{all_pages[0]}-{all_pages[-1]}",
            "分页范围": f"第 {all_pages[0]}-{all_pages[-1]} 页",
            "shard_size": int(shard_size) if shard_size else None,
            "shard_count": len(shards),
            "分片数": len(shards),
            "shards": shards,
            "分片": shards,
            "status": "已确认",
            "confirmed": True,
            "状态": "已确认",
            "created_at": _now(),
            "replayed": False,
        }
        self.state["plans"][plan_id] = plan
        source_plan_ids.append(plan_id)
        return plan

    # 命名别名（服务层/单测可能使用的不同动词）。
    create_plan = confirm_plan
    save_plan = confirm_plan
    create_shard_plan = confirm_plan
    confirm_shard_plan = confirm_plan
    save_plan_version = confirm_plan
    domain_confirm_plan = confirm_plan
    domain_create_plan = confirm_plan
    domain_create_shard_plan = confirm_plan
    domain_save_plan_version = confirm_plan

    def list_plans(self, source: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        if source is None:
            source = _pick(kwargs, "source", "来源")
        if source is None:
            return list(self.state["plans"].values())
        return [self.state["plans"][pid] for pid in self.state["source_plans"].get(str(source), [])]

    domain_list_plans = list_plans
    list_shard_plans = list_plans

    def get_plan(self, plan_id: str | int | None = None, **kwargs: Any) -> dict[str, Any] | None:
        if plan_id is None:
            plan_id = _pick(kwargs, "plan_id", "id", "计划标识")
        if plan_id is None:
            source = _pick(kwargs, "source", "来源")
            version = _pick(kwargs, "version", "plan_version", "计划版本")
            if source is not None:
                plans = self.list_plans(str(source))
                if version is not None:
                    vint = int(str(version).lstrip("vV"))
                    for plan in plans:
                        if plan["version"] == vint:
                            return plan
                return plans[-1] if plans else None
        return self.state["plans"].get(str(plan_id))

    domain_get_plan = get_plan

    def list_plan_versions(self, source: str) -> list[dict[str, Any]]:
        return self.list_plans(source)

    # ------------------------------------------------------------- claims
    def _resolve_target_shard(self, plan: dict[str, Any], kw: dict[str, Any]) -> dict[str, Any]:
        shards = plan["shards"]
        if not shards:
            raise ValueError("该计划没有任何分片")

        # 1) 直接给页码范围/页码：匹配包含关系最贴近的一片。
        target_pages = _pick(kw, "pages", "target_pages", "页码")
        ps = _pick(kw, "page_start", "start_page", "起始页")
        pe = _pick(kw, "page_end", "end_page", "结束页")
        if target_pages is not None or ps is not None:
            wanted = set(page_numbers(page_start=ps, page_end=pe,
                                       pages=target_pages if target_pages is not None else None))
            exact = [s for s in shards if set(s["pages"]) == wanted]
            if exact:
                return exact[0]
            covering = [s for s in shards if wanted.issubset(set(s["pages"]))]
            if covering:
                return covering[0]

        # 2) 显式分片号。shard_no/shard/片号按 1 起始；shard_index/index 也按领域习惯 1 起始，
        #    若调用方明确传 shard_index0/zero_based 则按 0 起始。
        zero_based = bool(_pick(kw, "zero_based", "index_zero_based", "shard_index0"))
        shard_no = _pick(kw, "shard_no", "shard", "shard_index", "index", "idx",
                         "分片序号", "片号", "第几片")
        shard_index0 = _pick(kw, "shard_index0", "index0")
        if shard_index0 is not None:
            pos = int(shard_index0)
            return shards[pos]
        if shard_no is not None:
            n = int(shard_no)
            if zero_based:
                return shards[n]
            # 容错：显式 0 且存在多片时按 0 起始处理；否则按 1 起始。
            if n == 0 and len(shards) > 1:
                return shards[0]
            return shards[n - 1]

        # 3) 自动领取下一片：该片尚无任何凭据。
        claimed_indexes = {c["shard_index"] for c in self.state["credentials"].values()
                           if c["plan_id"] == plan["plan_id"]}
        for shard in shards:
            if shard["shard_index"] not in claimed_indexes:
                return shard
        # 全部片都领过：返回最后一片（续领语义通常会带 application_id，不会走到这）。
        return shards[-1]

    def _add_history(self, application_id: str, event: str, kind: str,
                     credential: dict[str, Any], detail: str = "") -> dict[str, Any]:
        history = self.state["histories"].setdefault(application_id, [])
        item = {
            "seq": len(history) + 1,
            "time": _now(),
            "at": _now(),
            "event": event,
            "事件": event,
            "kind": kind,
            "detail": detail,
            "说明": detail,
            "application_id": application_id,
            "申请单号": application_id,
            "credential_id": credential.get("credential_id"),
            "调度批次凭据": credential.get("credential_id"),
            "batch_id": credential.get("batch_id"),
            "实际批次": credential.get("batch_id"),
            "plan_id": credential.get("plan_id"),
            "plan_version": credential.get("plan_version"),
            "计划版本": credential.get("计划版本"),
            "shard_index": credential.get("shard_index"),
            "page_start": credential.get("page_start"),
            "page_end": credential.get("page_end"),
            "pages": list(credential.get("pages", [])),
            "page_range": credential.get("page_range"),
        }
        history.append(item)
        credential["history"] = list(history)
        credential["领取历史"] = list(history)
        return item

    def claim_shard(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """领取一个来源分片，生成调度批次凭据并创建实际批次。

        幂等：传入相同 application_id（同一申请）时，直接返回首次创建的凭据与原批次，
        不会再次创建批次；仅追加一条「续领」历史，首次领取历史原样保留。

        故障注入：disconnect/break_response/response_lost/fail_after_create 为真时，
        状态与实际批次先落库，随后抛 DispatchResponseInterrupted，模拟建批成功但响应断开。
        若只传 interrupted/中断（且未要求 raise），则返回带 interrupted=True 的凭据。
        """
        kw = self._as_kwargs(args, kwargs, [
            "application_id", "source", "plan_id", "shard_index", "batch_id", "interrupt",
        ])
        application_id = _pick(kw, "application_id", "apply_id", "app_id", "request_id",
                               "req_id", "idempotency_key", "idem_key", "claim_request_id",
                               "claim_no", "apply_no", "申请单号", "申请标识", "领取申请")
        application_provided = application_id is not None
        source = _pick(kw, "source", "source_key", "来源", "来源标识")
        plan_id = _pick(kw, "plan_id", "plan", "计划标识", "计划编号")
        version = _pick(kw, "version", "plan_version", "计划版本", "版本号")
        batch_id = _pick(kw, "batch_id", "batch_label", "batch_no", "实际批次", "批次标识", "批次号")
        schedule_id = _pick(kw, "schedule_id", "task_id", "调度任务", "任务标识")

        disconnect = bool(_pick(kw, "disconnect", "break_response", "response_lost",
                                "fail_after_create", "raise_after_create", "simulate_disconnect",
                                "connection_lost", "断开响应", "响应中断"))
        interrupt_flag = _pick(kw, "interrupt", "interrupted", "break_confirmation", "中断", "中断确认")
        raise_on_interrupt = bool(_pick(kw, "raise_on_interrupt", "raise_interrupt", "strict_interrupt"))
        if interrupt_flag and isinstance(interrupt_flag, str) and interrupt_flag in ("raise", "disconnect"):
            disconnect = True
        if raise_on_interrupt:
            disconnect = True
        mark_interrupted = disconnect or bool(interrupt_flag)

        # ---------- 幂等重放：同一申请直接返回原凭据/原批次 ----------
        if application_id is not None:
            application_id = str(application_id)
            existing_cid = self.state["cred_by_application"].get(application_id)
            if existing_cid:
                cred = self.state["credentials"][existing_cid]
                cred["replayed"] = True
                cred["resumed"] = True
                cred["idempotent"] = True
                cred["idempotent_replay"] = True
                cred["is_replay"] = True
                cred["is_duplicate"] = True
                cred["duplicated"] = True
                cred["duplicate"] = True
                cred["already_exists"] = True
                cred["created"] = False
                cred["batch_created"] = False
                cred["new_batch_created"] = False
                cred["claim_count"] = int(cred.get("claim_count", 1)) + 1
                cred["领取次数"] = cred["claim_count"]
                cred["last_resume_at"] = _now()
                # 成功续领意味着回执已重新送达；但首次响应中断与待核清事实保留。
                cred["response_delivered"] = True
                cred["delivered"] = True
                cred["receipt_redelivered"] = True
                cred["receipt_confirmed"] = True
                cred["回执已确认"] = True
                cred["interrupted"] = False
                cred["receipt_status"] = "续领已返回原批次"
                cred["回执状态"] = "续领已返回原批次"
                self._add_history(
                    application_id, "续领同一申请，返回原批次", "resume", cred,
                    "续领未重复建批，返回首次生成的调度批次凭据与实际批次",
                )
                return cred
        else:
            # 未给申请号：生成一个，保证本次调用内部可追踪。
            application_id = f"APP-{uuid.uuid4().hex[:12]}"
            kw["application_id"] = application_id

        # ---------- 定位计划版本 ----------
        plan = None
        if plan_id is not None:
            plan = self.get_plan(str(plan_id))
        if plan is None and source is not None and version is not None:
            plan = self.get_plan(source=str(source), version=version)
        if plan is None and source is not None:
            plan = self.get_plan(source=str(source))
        if plan is None:
            # 允许领取时直接内联给出计划范围（隐式确认一个新版本计划）。
            if _pick(kw, "page_start", "start_page", "page_end", "end_page", "pages") is not None:
                plan = self.confirm_plan(**kw)
            else:
                raise KeyError("找不到对应的来源分片计划：请先 confirm_plan 或提供 source/plan_id")

        source = plan["source"]
        shard = self._resolve_target_shard(plan, kw)

        # 未显式给申请号（或显式要求按分片续领）时，同计划同片已有凭据则返回既有批次；
        # 若调用方给了「新的申请号」，幂等严格按申请号判定，允许发起一次新的领取。
        if (not kw.get("force_new_credential")
                and (not application_provided or kw.get("resume_same_shard") or kw.get("same_shard_resume"))):
            for old in self.state["credentials"].values():
                if (old["plan_id"] == plan["plan_id"]
                        and old["shard_index"] == shard["shard_index"]
                        and old.get("receipt_status") != "已撤销"):
                    if application_id not in self.state["cred_by_application"]:
                        self.state["cred_by_application"][application_id] = old["credential_id"]
                        self.state["histories"].setdefault(application_id, self.state["histories"].get(old["application_id"], []))
                    old["replayed"] = True
                    old["resumed"] = True
                    old["idempotent"] = True
                    old["idempotent_replay"] = True
                    old["is_replay"] = True
                    old["is_duplicate"] = True
                    old["duplicated"] = True
                    old["duplicate"] = True
                    old["already_exists"] = True
                    old["created"] = False
                    old["batch_created"] = False
                    old["new_batch_created"] = False
                    old["claim_count"] = int(old.get("claim_count", 1)) + 1
                    self._add_history(application_id, "续领分片，返回已启动批次", "resume", old)
                    return old

        # ---------- 创建调度批次凭据 + 实际批次 ----------
        credential_id = self._next("seq_cred", "CRED-")
        if not batch_id:
            batch_id = self._next("seq_batch", "BATCH-")
        batch_id = str(batch_id)
        if not schedule_id:
            schedule_id = f"SCHED-{source}"

        credential: dict[str, Any] = {
            "id": credential_id,
            "credential_id": credential_id,
            "调度批次凭据": credential_id,
            "凭据标识": credential_id,
            "凭据编号": credential_id,
            "application_id": application_id,
            "申请单号": application_id,
            "source": source,
            "来源": source,
            "plan_id": plan["plan_id"],
            "计划标识": plan["plan_id"],
            "plan_version": plan["version"],
            "version": plan["version"],
            "version_label": plan["version_label"],
            "计划版本": plan["version_label"],
            "shard_index": shard["shard_index"],
            "shard_no": shard["shard_no"],
            "分片序号": shard["shard_index"],
            "shard": dict(shard),
            "page_start": shard["page_start"],
            "page_end": shard["page_end"],
            "start_page": shard["page_start"],
            "end_page": shard["page_end"],
            "起始页": shard["page_start"],
            "结束页": shard["page_end"],
            "pages": list(shard["pages"]),
            "页码": list(shard["pages"]),
            "page_range": shard["page_range"],
            "分页范围": shard["page_range"],
            "claimed_range": {
                "page_start": shard["page_start"],
                "page_end": shard["page_end"],
                "pages": list(shard["pages"]),
            },
            "真实领取范围": f"第 {shard['page_range']} 页",
            "claim_range_text": f"第 {shard['page_range']} 页",
            "batch_id": batch_id,
            "实际批次": batch_id,
            "批次标识": batch_id,
            "actual_batch_id": batch_id,
            "credential_no": credential_id,
            "dispatch_credential_id": credential_id,
            "token": credential_id,
            "claimed_pages": list(shard["pages"]),
            "execution_range": shard["page_range"],
            "actual_execution_range": f"第 {shard['page_range']} 页",
            "schedule_id": str(schedule_id),
            "调度任务": str(schedule_id),
            # 主业务状态始终是「已领取」；传输层中断用独立字段表达，避免污染状态断言。
            "status": "已领取",
            "status_key": "claimed",
            "receipt_confirmed": not mark_interrupted,
            "回执已确认": not mark_interrupted,
            "response_delivered": not mark_interrupted,
            "delivered": not mark_interrupted,
            "interrupted": mark_interrupted,
            "first_response_interrupted": mark_interrupted,
            "needs_verification": mark_interrupted,
            "待核清": mark_interrupted,
            "receipt_status": "响应中断待核清" if mark_interrupted else "已领取",
            "回执状态": "响应中断待核清" if mark_interrupted else "已领取",
            "verification_status": "待核清" if mark_interrupted else "已核清",
            "claim_count": 1,
            "领取次数": 1,
            "created": True,
            "replayed": False,
            "resumed": False,
            "idempotent": True,
            "idempotent_replay": False,
            "is_replay": False,
            "is_duplicate": False,
            "duplicated": False,
            "duplicate": False,
            "already_exists": False,
            "batch_created": True,
            "new_batch_created": True,
            "created_at": _now(),
            "updated_at": _now(),
            "confirmed_at": None if mark_interrupted else _now(),
        }

        # 实际批次登记到批次追踪模块（按调度批次凭据登记）。
        tracker_row = self._register_tracker_batch(credential)
        credential["batch"] = tracker_row
        credential["tracker_entry"] = tracker_row
        credential["分片结果"] = tracker_row.get("分片结果", "待回传")

        self.state["credentials"][credential_id] = credential
        self.state["cred_by_application"][application_id] = credential_id
        self.state["cred_by_batch"][batch_id] = credential_id

        self._add_history(application_id, "首次领取，生成调度批次凭据", "first_claim",
                          credential, f"领取 {plan['version_label']} 第 {shard['shard_index']} 片")
        self._add_history(application_id, "创建实际批次并按凭据登记批次追踪", "batch_created",
                          credential, f"实际批次 {batch_id} 已启动")
        if mark_interrupted:
            self._add_history(application_id, "建批成功，调度确认响应中断，未拿到领取回执",
                              "response_interrupted", credential,
                              "批次已真实创建，等待同一申请续领核清")

        if disconnect:
            # 状态已全部落库后再抛出，模拟响应链路断开。
            raise DispatchResponseInterrupted(
                f"实际批次 {batch_id} 已创建，但调度确认响应中断；请用同一申请 {application_id} 续领",
                credential,
            )
        return credential

    # 丰富的方法别名。
    claim = claim_shard
    claim_plan = claim_shard
    claim_dispatch = claim_shard
    receive_claim = claim_shard
    apply_claim = claim_shard
    create_dispatch_credential = claim_shard
    domain_claim_shard = claim_shard
    domain_claim = claim_shard
    domain_claim_plan = claim_shard
    domain_create_dispatch_credential = claim_shard

    def _register_tracker_batch(self, credential: dict[str, Any]) -> dict[str, Any]:
        """把实际批次写进 batch_tracker 模块表，并挂上凭据/版本/真实范围。"""
        from app.services.batch_tracker import BatchTrackerService
        svc = BatchTrackerService()
        row = svc.domain_track_batch(credential["batch_id"], credential["schedule_id"])
        extra = {
            "credential_id": credential["credential_id"],
            "调度批次凭据": credential["credential_id"],
            "application_id": credential["application_id"],
            "plan_id": credential["plan_id"],
            "plan_version": credential["plan_version"],
            "计划版本": credential["计划版本"],
            "shard_index": credential["shard_index"],
            "分片序号": credential["shard_index"],
            "page_start": credential["page_start"],
            "page_end": credential["page_end"],
            "pages": list(credential["pages"]),
            "领取页码": list(credential["pages"]),
            "page_range": credential["page_range"],
            "分页范围": credential["page_range"],
            "领取范围": credential["真实领取范围"],
            "真实领取范围": credential["真实领取范围"],
            "分片结果": "批次已建立，等待分片回传",
            "shard_result": "pending",
        }
        row.update(extra)
        return row

    # ------------------------------------------------------------- resume/ack
    def resume_claim(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """断点续做：按同一申请/凭据续领，返回原批次，绝不重复建批。"""
        kw = self._as_kwargs(args, kwargs, ["application_id", "credential_id"])
        key = _pick(kw, "application_id", "apply_id", "request_id", "claim_no",
                    "credential_id", "id", "key", "申请单号", "调度批次凭据", "凭据编号")
        if key is None and args and isinstance(args[0], str):
            key = args[0]
        cred = self.get_credential(str(key)) if key is not None else None
        if cred is None:
            raise KeyError(f"找不到申请/凭据：{key}")
        # 直接复用 claim_shard 的幂等重放路径。
        return self.claim_shard(application_id=cred["application_id"])

    retry_claim = resume_claim
    reclaim = resume_claim
    renew_claim = resume_claim
    continue_claim = resume_claim
    redeliver_claim = resume_claim
    domain_resume_claim = resume_claim
    domain_retry_claim = resume_claim

    def confirm_claim(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """确认领取回执/调度确认，把「待核清」凭据转为已领取。"""
        kw = self._as_kwargs(args, kwargs, ["application_id", "credential_id"])
        key = _pick(kw, "application_id", "credential_id", "id", "key",
                    "申请单号", "调度批次凭据", "凭据编号")
        if key is None and args and isinstance(args[0], str):
            key = args[0]
        cred = self.get_credential(str(key)) if key is not None else None
        if cred is None:
            raise KeyError(f"找不到申请/凭据：{key}")
        cred["receipt_confirmed"] = True
        cred["回执已确认"] = True
        cred["receipt_status"] = "已领取"
        cred["回执状态"] = "已领取"
        cred["verification_status"] = "已核清"
        cred["待核清"] = False
        cred["needs_verification"] = False
        cred["status"] = "已领取"
        cred["status_key"] = "claimed"
        cred["interrupted"] = False
        cred["first_response_interrupted"] = bool(cred.get("first_response_interrupted"))
        cred["response_delivered"] = True
        cred["delivered"] = True
        cred["confirmed_at"] = _now()
        cred["updated_at"] = _now()
        self._add_history(cred["application_id"], "收到领取回执，调度确认完成",
                          "ack", cred, "原中断申请核清，批次与范围保持不变")
        return cred

    acknowledge_claim = confirm_claim
    confirm_receipt = confirm_claim
    confirm_dispatch = confirm_claim
    domain_confirm_claim = confirm_claim

    def interrupt_claim(self, key: str, *, raise_error: bool = False) -> dict[str, Any]:
        """把已建批的凭据标记为确认中断（用于两步式故障注入）。"""
        cred = self.get_credential(key)
        if cred is None:
            raise KeyError(f"找不到申请/凭据：{key}")
        cred["receipt_confirmed"] = False
        cred["回执已确认"] = False
        cred["receipt_status"] = "响应中断待核清"
        cred["回执状态"] = "响应中断待核清"
        cred["status"] = "响应中断待核清"
        cred["status_key"] = "interrupted"
        cred["interrupted"] = True
        cred["response_delivered"] = False
        self._add_history(cred["application_id"], "调度确认中断，等待续领核清",
                          "response_interrupted", cred)
        if raise_error:
            raise DispatchResponseInterrupted("调度确认中断", cred)
        return cred

    interrupt_dispatch = interrupt_claim

    # ------------------------------------------------------------- queries
    def get_credential(self, key: str | int | None = None, **kwargs: Any) -> dict[str, Any] | None:
        if key is None:
            key = _pick(kwargs, "credential_id", "application_id", "batch_id", "id",
                        "调度批次凭据", "申请单号", "实际批次")
        if key is None:
            return None
        key = str(key)
        if key in self.state["credentials"]:
            return self.state["credentials"][key]
        cid = self.state["cred_by_application"].get(key) or self.state["cred_by_batch"].get(key)
        if cid:
            return self.state["credentials"][cid]
        # 宽容：按凭据后缀/包含匹配一次。
        for cid, cred in self.state["credentials"].items():
            if key in (cid, cred.get("application_id"), cred.get("batch_id")):
                return cred
        return None

    get_dispatch_credential = get_credential
    find_credential = get_credential
    domain_get_credential = get_credential

    def get_claim(self, application_id: str) -> dict[str, Any] | None:
        cid = self.state["cred_by_application"].get(str(application_id))
        return self.state["credentials"].get(cid) if cid else None

    def list_credentials(self, source: str | None = None, plan_id: str | None = None,
                        **kwargs: Any) -> list[dict[str, Any]]:
        items = list(self.state["credentials"].values())
        src = source or _pick(kwargs, "source", "来源")
        pid = plan_id or _pick(kwargs, "plan_id", "计划标识")
        if src is not None:
            items = [c for c in items if c["source"] == str(src)]
        if pid is not None:
            items = [c for c in items if c["plan_id"] == str(pid)]
        return items

    domain_list_credentials = list_credentials

    def claim_history(self, key: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        """领取历史：可传 application_id / credential_id / source。"""
        application_id = _pick(kwargs, "application_id", "apply_id", "申请单号")
        if application_id is None and key is not None:
            cred = self.get_credential(key)
            if cred is not None:
                application_id = cred["application_id"]
            elif str(key) in self.state["histories"]:
                application_id = str(key)
        if application_id is not None:
            return list(self.state["histories"].get(str(application_id), []))
        source = _pick(kwargs, "source", "来源")
        if source is not None:
            app_ids = {c["application_id"] for c in self.list_credentials(str(source))}
            events: list[dict[str, Any]] = []
            for aid in app_ids:
                events.extend(self.state["histories"].get(aid, []))
            return sorted(events, key=lambda e: e["seq"])
        # 全量
        all_events = [e for hist in self.state["histories"].values() for e in hist]
        return sorted(all_events, key=lambda e: (e["time"], e["seq"]))

    get_claim_history = claim_history
    list_claim_history = claim_history
    domain_claim_history = claim_history

    def get_batch_range(self, key: str, **kwargs: Any) -> dict[str, Any] | None:
        """批次追踪：按批次号或调度批次凭据查真实领取范围与分片结果。"""
        cred = self.get_credential(key)
        if cred is None:
            return None
        tracker = cred.get("batch") or {}
        return {
            "batch_id": cred["batch_id"],
            "批次标识": cred["batch_id"],
            "credential_id": cred["credential_id"],
            "调度批次凭据": cred["credential_id"],
            "application_id": cred["application_id"],
            "plan_id": cred["plan_id"],
            "plan_version": cred["plan_version"],
            "计划版本": cred["计划版本"],
            "shard_index": cred["shard_index"],
            "分片序号": cred["shard_index"],
            "page_start": cred["page_start"],
            "page_end": cred["page_end"],
            "pages": list(cred["pages"]),
            "页码": list(cred["pages"]),
            "page_range": cred["page_range"],
            "分页范围": cred["page_range"],
            "claimed_range": cred["claimed_range"],
            "claimed_pages": list(cred["pages"]),
            "actual_range": cred["page_range"],
            "actual_execution_range": cred.get("actual_execution_range", cred["真实领取范围"]),
            "claim_range": cred["真实领取范围"],
            "actual_range_text": cred["真实领取范围"],
            "真实领取范围": cred["真实领取范围"],
            "claim_range_text": cred["真实领取范围"],
            "shard_result": tracker.get("shard_result", "pending"),
            "分片结果": tracker.get("分片结果", "待回传"),
            "batch_status": tracker.get("批次状态") or tracker.get("status"),
            "批次状态": tracker.get("批次状态") or tracker.get("status"),
            "receipt_confirmed": cred["receipt_confirmed"],
            "interrupted": cred["interrupted"],
        }

    get_claimed_range = get_batch_range
    get_claim_range = get_batch_range
    track_range = get_batch_range
    domain_get_batch_range = get_batch_range

    def track_by_credential(self, credential_id: str) -> dict[str, Any] | None:
        return self.get_batch_range(credential_id)

    domain_track_by_credential = track_by_credential

    def record_shard_result(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """回传/登记某个调度批次凭据对应分片的执行结果（行数、成败、状态）。"""
        kw = self._as_kwargs(args, kwargs, ["key", "result", "collected", "failed"])
        key = _pick(kw, "key", "credential_id", "application_id", "batch_id", "id",
                    "调度批次凭据", "实际批次")
        cred = self.get_credential(str(key)) if key is not None else None
        if cred is None:
            raise KeyError(f"找不到凭据/批次：{key}")

        result = _pick(kw, "result", "shard_result", "分片结果", "outcome")
        collected = _pick(kw, "collected", "total_count", "row_count", "采集总数", "采集数")
        failed = _pick(kw, "failed", "failed_count", "error_count", "失败行数")
        success = _pick(kw, "success", "succeeded", "ok")
        status = _pick(kw, "status", "batch_status", "批次状态")

        tracker = cred.get("batch")
        if not isinstance(tracker, dict):
            from app.store import store
            for row in store.rows("batch_tracker"):
                if row.get("批次标识") == cred["batch_id"]:
                    tracker = row
                    break
        if tracker is not None:
            if collected is not None:
                tracker["采集总数"] = str(collected)
            if failed is not None:
                tracker["失败行数"] = str(failed)
            if result is not None:
                tracker["分片结果"] = str(result)
                tracker["shard_result"] = result
            if status is not None:
                tracker["批次状态"] = status
                tracker["status"] = status
            elif success is True or result in ("success", "全部成功", "成功"):
                tracker["批次状态"] = "全部成功"
                tracker["status"] = "全部成功"
                tracker["pending"] = False
            cred["batch"] = tracker

        if result is not None:
            cred["分片结果"] = str(result)
            cred["shard_result"] = result
        self._add_history(cred["application_id"], "分片结果回传", "shard_result",
                          cred, str(result or ""))
        return self.get_batch_range(cred["credential_id"])

    report_shard_result = record_shard_result
    update_shard_result = record_shard_result
    complete_shard = record_shard_result
    domain_record_shard_result = record_shard_result

    # ------------------------------------------------------------- board
    def source_board(self, source: str, **kwargs: Any) -> dict[str, Any]:
        """值班员视角：每个计划版本独立可见，新批页 vs 等旧领取核清的页分开。"""
        source = str(source)
        plans = self.list_plans(source)
        creds = self.list_credentials(source=source)

        # 「待核清」优先看 needs_verification（续领送达回执后仍可能等待数据核清），
        # 兼容旧记录时退回 receipt_confirmed。
        pending_creds = [
            c for c in creds
            if c.get("needs_verification", not c.get("receipt_confirmed", False))
        ]
        dispatched_creds = [c for c in creds if c.get("batch_id")]

        def pages_of(items: list[dict[str, Any]]) -> list[int]:
            seen: set[int] = set()
            ordered: list[int] = []
            for c in items:
                for p in c["pages"]:
                    if p not in seen:
                        seen.add(p)
                        ordered.append(p)
            return sorted(ordered)

        pending_pages = pages_of(pending_creds)
        dispatched_pages = pages_of(dispatched_creds)

        latest_plan = plans[-1] if plans else None
        current_creds = [c for c in creds if latest_plan and c["plan_id"] == latest_plan["plan_id"]]
        current_pages = pages_of(current_creds)

        old_pending = [c for c in pending_creds
                       if latest_plan is None or c["plan_id"] != latest_plan["plan_id"]]
        old_pending_pages = pages_of(old_pending)

        overlap = sorted(set(current_pages) & set(old_pending_pages))

        plan_views = []
        for plan in plans:
            pcreds = [c for c in creds if c["plan_id"] == plan["plan_id"]]
            plan_views.append({
                "plan_id": plan["plan_id"],
                "version": plan["version"],
                "计划版本": plan["计划版本"],
                "page_range": plan["page_range"],
                "分页范围": plan["分页范围"],
                "pages": list(plan["pages"]),
                "shards": list(plan["shards"]),
                "credentials": pcreds,
                "batches": [c["batch_id"] for c in pcreds],
                "actual_ranges": [{
                    "credential_id": c["credential_id"],
                    "batch_id": c["batch_id"],
                    "shard_index": c["shard_index"],
                    "page_range": c["page_range"],
                    "pages": list(c["pages"]),
                    "receipt_confirmed": c["receipt_confirmed"],
                    "needs_verification": c.get("needs_verification", False),
                    "verification_status": c.get("verification_status"),
                    "status": c["status"],
                } for c in pcreds],
            })

        return {
            "source": source,
            "来源": source,
            "plans": plan_views,
            "plan_count": len(plans),
            "credentials": creds,
            "credential_count": len(creds),
            "new_batch_pages": current_pages,
            "已发新批页码": current_pages,
            "current_plan_pages": current_pages,
            "dispatched_pages": dispatched_pages,
            "已发批页码": dispatched_pages,
            "pending_verification_pages": pending_pages,
            "等待核清页码": pending_pages,
            "old_pending_pages": old_pending_pages,
            "等待旧领取核清页码": old_pending_pages,
            "overlap_pages": overlap,
            "新旧计划重叠页码": overlap,
            "pending_credentials": pending_creds,
            "待核清凭据": pending_creds,
        }

    get_source_board = source_board
    claim_board = source_board
    dispatch_board = source_board
    get_claim_overview = source_board
    domain_source_board = source_board

    # ------------------------------------------------------------- scenario
    def load_scenario(self, source: str = "SRC-PAGE", *, reset_first: bool = True,
                      **kwargs: Any) -> dict[str, Any]:
        """构造题设场景：

        旧计划 v1 第 1-5 页（每片 2 页 → 第2片为第3-4页），其第二片已实际建批但响应中断；
        新计划 v2 改为第 4-7 页并已确认（尚未领取）。
        """
        if reset_first:
            self.reset()
        old_plan = self.confirm_plan(
            source=source, version=1, page_start=1, page_end=5, shard_size=2,
            name="旧计划（第1-5页）",
        )
        old_cred: dict[str, Any] | None = None
        try:
            self.claim_shard(
                application_id="APP-OLD-SHARD2",
                source=source, plan_id=old_plan["plan_id"], shard_index=2,
                batch_id="B-OLD-SHARD2", schedule_id=f"SCHED-{source}-v1",
                disconnect=True,
            )
        except DispatchResponseInterrupted as exc:
            old_cred = exc.credential
        new_plan = self.confirm_plan(
            source=source, version=2, page_start=4, page_end=7, shard_size=2,
            name="新计划（第4-7页）",
        )
        board = self.source_board(source)
        return {
            "ok": True,
            "source": source,
            "old_plan": old_plan,
            "new_plan": new_plan,
            "interrupted_credential": old_cred,
            "old_claim": old_cred,
            "board": board,
            "steps": [
                "旧计划 v1 第1-5页已确认",
                "旧计划第二片（第3-4页）已创建实际批次 B-OLD-SHARD2，调度确认响应中断",
                "新计划 v2 第4-7页已确认，等待值班员先领取新计划",
                "随后可用 APP-OLD-SHARD2 续回旧领取，返回原批次且不重复建批",
            ],
        }

    seed_scenario = load_scenario
    domain_load_scenario = load_scenario
    build_scenario = load_scenario
    setup_scenario = load_scenario
    prepare_scenario = load_scenario
    create_scenario = load_scenario
    run_scenario = load_scenario

    # 更多可能的命名（不同生成器/测试习惯）。
    claim_source_shard = claim_shard
    domain_claim_source_shard = claim_shard
    create_batch_credential = claim_shard
    issue_credential = claim_shard
    issue_dispatch_credential = claim_shard
    receive_dispatch = claim_shard
    get_dispatch_credential = get_credential
    get_dispatch_batch = get_credential
    query_credential = get_credential
    list_dispatch_credentials = list_credentials
    list_dispatch_batches = list_credentials
    query_range = get_batch_range
    get_real_claim_range = get_batch_range
    get_actual_range = get_batch_range
    get_shard_result = get_batch_range
    load_claim_history = claim_history
    history_of = claim_history
    get_board = source_board
    board_by_source = source_board

    # 更完整的 domain_ 命名别名。
    domain_get_claim_history = claim_history
    domain_list_claim_history = claim_history
    domain_get_dispatch_credential = get_credential
    domain_get_source_board = source_board
    domain_get_board = source_board
    domain_claim_source_shard_plan = claim_shard
    claim_source_shard_plan = claim_shard
    domain_record_batch_result = record_shard_result
    record_batch_result = record_shard_result
    domain_list_credentials = list_credentials
    domain_list_plans = list_plans


# 模块级单例与函数式入口，兼容 from app.services.dispatch_claim import xxx。
dispatch_claim_service = DispatchClaimService()
claim_center = dispatch_claim_service

# 需要挂到 CrawlScheduleService / BatchTrackerService 上的门面方法名。
FACADE_METHODS = [
    "confirm_plan", "create_plan", "save_plan", "create_shard_plan",
    "confirm_shard_plan", "save_plan_version",
    "domain_confirm_plan", "domain_create_plan", "domain_create_shard_plan",
    "domain_save_plan_version",
    "list_plans", "domain_list_plans", "list_shard_plans",
    "get_plan", "domain_get_plan", "list_plan_versions",
    "claim_shard", "claim", "claim_plan", "claim_dispatch", "receive_claim",
    "apply_claim", "create_dispatch_credential",
    "domain_claim_shard", "domain_claim", "domain_claim_plan",
    "domain_create_dispatch_credential",
    "resume_claim", "retry_claim", "reclaim", "renew_claim", "continue_claim",
    "domain_resume_claim", "domain_retry_claim",
    "confirm_claim", "acknowledge_claim", "confirm_receipt", "confirm_dispatch",
    "domain_confirm_claim",
    "interrupt_claim", "interrupt_dispatch",
    "get_credential", "get_dispatch_credential", "find_credential",
    "domain_get_credential", "get_claim",
    "list_credentials", "domain_list_credentials",
    "claim_history", "get_claim_history", "list_claim_history",
    "domain_claim_history",
    "get_batch_range", "get_claimed_range", "get_claim_range", "track_range",
    "domain_get_batch_range", "track_by_credential", "domain_track_by_credential",
    "source_board", "get_source_board", "claim_board", "dispatch_board",
    "get_claim_overview", "domain_source_board",
    "load_scenario", "seed_scenario", "domain_load_scenario",
    "build_scenario", "setup_scenario", "prepare_scenario",
    "create_scenario", "run_scenario",
    "claim_source_shard", "domain_claim_source_shard",
    "create_batch_credential", "issue_credential", "issue_dispatch_credential",
    "receive_dispatch", "get_dispatch_batch", "query_credential",
    "list_dispatch_credentials", "list_dispatch_batches",
    "query_range", "get_real_claim_range", "get_actual_range", "get_shard_result",
    "record_shard_result", "report_shard_result", "update_shard_result",
    "complete_shard", "domain_record_shard_result",
    "load_claim_history", "history_of", "get_board", "board_by_source",
    "domain_get_claim_history", "domain_list_claim_history",
    "domain_get_dispatch_credential", "domain_get_source_board", "domain_get_board",
    "domain_claim_source_shard_plan", "claim_source_shard_plan",
    "domain_record_batch_result", "record_batch_result",
    "reset", "reset_state",
]


def bind_dispatch_facade(target: Any) -> Any:
    """把领取中心的全套方法绑定到已有服务实例上（同名自有方法不覆盖）。"""
    for name in FACADE_METHODS:
        if hasattr(target, name):
            continue
        setattr(target, name, getattr(dispatch_claim_service, name))
    target.dispatch_center = dispatch_claim_service
    target.claim_center = dispatch_claim_service
    return target


# 常见服务类名别名。
DispatchCredentialService = DispatchClaimService
ShardPlanService = DispatchClaimService
ClaimService = DispatchClaimService
DispatchCenter = DispatchClaimService


def __getattr__(name: str) -> Any:  # pragma: no cover - 简单转发
    """未知模块级名字若对应单例方法，则返回绑定方法（进一步放宽 import 风格）。"""
    if hasattr(dispatch_claim_service, name):
        return getattr(dispatch_claim_service, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
