"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.dispatch_claim import bind_dispatch_facade, claim_center


class CrawlScheduleService(BaseModuleService):
    MODULE = "crawl_schedule"
    ENTITY = "调度任务"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["任务标识", "关联配置", "调度周期", "起始水位", "截止水位", "分片策略", "上次执行", "下次执行"]
    REQUIRED_FIELDS = ["任务标识", "关联配置", "调度周期"]
    STATUS_ORDER = ["待启动", "运行中", "已完成", "调度失败"]
    ACTION_RULES = {"启动调度": "待启动", "取消调度": "调度失败"}
    NEGATIVE_ACTIONS = []

    def __init__(self) -> None:
        # 挂接「来源分片计划 + 调度批次凭据 + 领取历史」全套门面方法。
        bind_dispatch_facade(self)

    def domain_create_schedule(self, config_name: str, period: str = "daily") -> dict:
        """生成采集调度计划。"""
        from app.store import store
        import time
        rows = store.rows(self.MODULE)
        task_id = f"SCHED-{max((int(r.get('id', 0)) for r in rows), default=0) + 1}"
        plan = {
            "task_id": task_id,
            "config_name": config_name,
            "period": period,
            "shard_count": 4,
            "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "watermark_start": "2026-01-01",
            "watermark_end": time.strftime("%Y-%m-%d"),
        }
        return plan

    # 让通用动作入口也能识别领取类动作（前端行内按钮 /api/crawl_schedule/{id}/actions）。
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        claim_verbs = {
            "领取分片": "claim", "领取": "claim", "续领": "resume",
            "重试领取": "resume", "断点续做": "resume", "确认回执": "confirm",
        }
        if action in claim_verbs:
            entry = self.get_entry(entry_id) or {"id": entry_id}
            application_id = f"ROW-{entry_id}-{claim_verbs[action]}"
            try:
                if claim_verbs[action] == "claim":
                    result = claim_center.claim_shard(
                        application_id=application_id,
                        source=str(entry.get("关联配置") or entry.get("任务标识") or entry_id),
                    )
                elif claim_verbs[action] == "resume":
                    result = claim_center.resume_claim(application_id)
                else:
                    result = claim_center.confirm_claim(application_id)
                if isinstance(entry, dict):
                    entry["last_credential"] = result.get("credential_id")
                return entry, f"{action}完成：{result.get('credential_id')} / {result.get('batch_id')}"
            except Exception as exc:  # noqa: BLE001 - 动作接口需要把失败转成消息
                return entry, f"{action}未完成：{exc}"
        return super().run_action(entry_id, action)


# 模块级函数式入口，兼容 from app.services.crawl_schedule import claim_shard 等写法。
def __getattr__(name: str) -> Any:
    if hasattr(claim_center, name):
        return getattr(claim_center, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
