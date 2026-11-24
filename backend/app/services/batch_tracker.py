"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.dispatch_claim import bind_dispatch_facade, claim_center


class BatchTrackerService(BaseModuleService):
    MODULE = "batch_tracker"
    ENTITY = "采集批次"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["批次标识", "调度任务", "开始时间", "结束时间", "采集总数", "失败行数", "水位标记", "批次状态"]
    REQUIRED_FIELDS = ["批次标识", "调度任务", "开始时间"]
    STATUS_ORDER = ["执行中", "部分成功", "全部成功", "采集失败"]
    ACTION_RULES = {"重采失败行": "部分成功", "标记完成": "全部成功"}
    NEGATIVE_ACTIONS = []

    def __init__(self) -> None:
        bind_dispatch_facade(self)

    def domain_track_batch(self, batch_id: str, schedule_id: str,
                           credential_id: str | None = None, **extra: Any) -> dict:
        """追踪采集批次执行状态。

        传入 credential_id（调度批次凭据）时，按凭据补登计划版本、真实领取范围与分片结果。
        同一 batch_id 重复登记时幂等返回既有行，不重复追加。
        """
        from app.store import store
        import time
        rows = store.rows(self.MODULE)

        # 幂等：同批次标识已登记则直接返回（建批响应断开后续领不会产生第二条追踪）。
        for existing in rows:
            if existing.get("批次标识") == batch_id:
                if credential_id and not existing.get("credential_id"):
                    existing.update({"credential_id": credential_id, "调度批次凭据": credential_id})
                return existing

        now = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "批次标识": batch_id,
            "调度任务": schedule_id,
            "开始时间": now,
            "结束时间": None,
            "采集总数": "0",
            "失败行数": "0",
            "水位标记": now,
            "批次状态": "执行中",
            "status": "执行中",
            "pending": True,
            "abnormal": False,
        }
        if credential_id:
            entry.update({"credential_id": credential_id, "调度批次凭据": credential_id})
            tracked = claim_center.get_batch_range(credential_id)
            if tracked:
                entry.update({
                    "application_id": tracked["application_id"],
                    "plan_id": tracked["plan_id"],
                    "plan_version": tracked["plan_version"],
                    "计划版本": tracked["计划版本"],
                    "shard_index": tracked["shard_index"],
                    "分片序号": tracked["shard_index"],
                    "page_start": tracked["page_start"],
                    "page_end": tracked["page_end"],
                    "pages": tracked["pages"],
                    "领取页码": tracked["pages"],
                    "page_range": tracked["page_range"],
                    "分页范围": tracked["分页范围"],
                    "领取范围": tracked["真实领取范围"],
                    "真实领取范围": tracked["真实领取范围"],
                    "分片结果": tracked["分片结果"],
                })
        entry.update({k: v for k, v in extra.items() if v is not None})
        rows.append(entry)
        return entry

    def domain_track_by_credential(self, credential_id: str) -> dict | None:
        """按调度批次凭据查询真实领取范围与分片结果（含追踪行）。"""
        tracked = claim_center.get_batch_range(credential_id)
        if tracked is None:
            return None
        from app.store import store
        row = None
        for item in store.rows(self.MODULE):
            if item.get("credential_id") == credential_id or item.get("批次标识") == tracked["batch_id"]:
                row = item
                break
        tracked["tracker_entry"] = row
        return tracked

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        # 标记完成时顺带把分片结果置为全部成功，保持追踪信息一致。
        entry = self.get_entry(entry_id)
        result_entry, message = super().run_action(entry_id, action)
        if result_entry is not None and action == "标记完成":
            result_entry["分片结果"] = "全部分片采集成功"
            result_entry["shard_result"] = "success"
        return result_entry, message


def __getattr__(name: str) -> Any:
    if hasattr(claim_center, name):
        return getattr(claim_center, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
