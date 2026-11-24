"""跨模块业务链路：多条业务链把多个领域服务串成完整流程。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.services.crawler_config import CrawlerConfigService
from app.services.crawl_schedule import CrawlScheduleService
from app.services.batch_tracker import BatchTrackerService
from app.services.result_store import ResultStoreService
from app.services.parser_rule import ParserRuleService
from app.services.field_mapper import FieldMapperService
from app.services.html_cleaner import HtmlCleanerService
from app.services.dedup_engine import DedupEngineService
from app.services.proxy_pool import ProxyPoolService
from app.services.rate_limiter import RateLimiterService
from app.services.error_retry import ErrorRetryService
from app.services.error_logger import ErrorLoggerService
from app.services.cookie_mgr import CookieMgrService
from app.services.source_auth import SourceAuthService
from app.services.session_manager import SessionManagerService
from app.services.export_channel import ExportChannelService
from app.services.crawl_audit import CrawlAuditService
from app.services.data_cleanup import DataCleanupService

router = APIRouter(prefix="/api/chains", tags=["业务链路"])

_crawler = CrawlerConfigService()
_schedule = CrawlScheduleService()
_batch = BatchTrackerService()
_store = ResultStoreService()
_parse = ParserRuleService()
_mapper = FieldMapperService()
_cleaner = HtmlCleanerService()
_dedup = DedupEngineService()
_proxy = ProxyPoolService()
_limiter = RateLimiterService()
_retry = ErrorRetryService()
_logger = ErrorLoggerService()
_cookie = CookieMgrService()
_auth = SourceAuthService()
_session = SessionManagerService()
_export = ExportChannelService()
_audit = CrawlAuditService()
_cleanup = DataCleanupService()


@router.post("/crawl-pipeline")
def run_crawl_pipeline(payload: dict[str, Any]) -> dict[str, Any]:
    """采集流水线：配置校验 → 生成调度 → 批次追踪 → 结果存储。"""
    config_id = payload.get("config_id", 1)
    config_name = payload.get("config_name", "default")
    period = payload.get("period", "daily")
    batch_label = payload.get("batch_label", "BATCH-1")
    row_count = payload.get("row_count", 100)
    valid_count = payload.get("valid_count", 95)

    vresult = _crawler.domain_validate_config(config_id)
    sresult = _schedule.domain_create_schedule(config_name, period)
    bresult = _batch.domain_track_batch(batch_label, sresult["task_id"])
    storeresult = _store.domain_store_result(batch_label, config_name, row_count, valid_count)

    return {
        "chain": "crawl-pipeline",
        "steps": {
            "validate_config": vresult,
            "create_schedule": sresult,
            "track_batch": bresult,
            "store_result": storeresult,
        },
    }


@router.post("/data-process")
def run_data_process(payload: dict[str, Any]) -> dict[str, Any]:
    """数据处理：HTML解析 → 字段映射 → HTML清洗 → 去重。"""
    html = payload.get("html", "<html><body><p>测试内容</p></body></html>")
    source_data = payload.get("source_data", {"name": "test", "count": "42"})
    mapping_rules = payload.get("mapping_rules", [
        {"source": "name", "target": "title", "default": "", "transform": ""},
        {"source": "count", "target": "total", "default": "0", "transform": "int"},
    ])
    key_fields = payload.get("key_fields", ["title"])

    parse_result = _parse.domain_parse_html(html)
    map_result = _mapper.domain_map_fields(source_data, mapping_rules)
    clean_result = _cleaner.domain_clean_html(html)
    items = [map_result, {"title": "extra", "other": "value"}]
    dedup_result = _dedup.domain_dedup(items, key_fields)

    return {
        "chain": "data-process",
        "steps": {
            "parse_html": parse_result,
            "map_fields": map_result,
            "clean_html": {"original_length": clean_result["original_length"], "cleaned_length": clean_result["cleaned_length"]},
            "dedup": {"original_count": dedup_result["original_count"], "deduped_count": dedup_result["deduped_count"], "removed_count": dedup_result["removed_count"]},
        },
    }


@router.post("/reliability")
def run_reliability(payload: dict[str, Any]) -> dict[str, Any]:
    """可靠性保障：代理分配 → 限速检查 → 重试计算 → 错误记录。"""
    site = payload.get("site", "example.com")
    protocol = payload.get("protocol", "http")
    request_count = payload.get("request_count", 100)
    error_type = payload.get("error_type", "timeout")
    attempt = payload.get("attempt", 2)

    proxy_result = _proxy.domain_allocate_proxy(protocol)
    rate_result = _limiter.domain_check_rate(site, request_count)
    retry_result = _retry.domain_retry_calc(error_type, attempt)
    log_result = _logger.domain_log_error(
        "ERR-CHECK", f"可靠性检查 {site}", site)

    return {
        "chain": "reliability",
        "steps": {
            "allocate_proxy": proxy_result,
            "check_rate": rate_result,
            "retry_calc": retry_result,
            "log_error": {"error_code": log_result["错误码"], "error_message": log_result["错误信息"]},
        },
    }


@router.post("/auth")
def run_auth(payload: dict[str, Any]) -> dict[str, Any]:
    """认证链路：Cookie获取 → 来源认证 → 会话创建。"""
    site = payload.get("site", "example.com")
    auth_type = payload.get("auth_type", "token")
    session_type = payload.get("session_type", "http")

    cookie_result = _cookie.domain_get_cookies(site)
    auth_result = _auth.domain_authenticate(site, auth_type)
    session_id = f"{site}-session-1"
    session_result = _session.domain_create_session(session_id, site, session_type)

    return {
        "chain": "auth",
        "steps": {
            "get_cookies": cookie_result,
            "authenticate": auth_result,
            "create_session": session_result,
        },
    }


@router.post("/delivery")
def run_delivery(payload: dict[str, Any]) -> dict[str, Any]:
    """交付链路：结果存储 → 导出 → 审计 → 数据清理。"""
    batch_id = payload.get("batch_id", "BATCH-DELIVERY-1")
    config_name = payload.get("config_name", "delivery")
    export_format = payload.get("export_format", "csv")
    fields = payload.get("fields", ["id", "title", "count"])
    cleanup_module = payload.get("cleanup_module", "result_store")

    store_result = _store.domain_store_result(batch_id, config_name, 200, 180)
    export_result = _export.domain_export(batch_id, export_format, fields)
    audit_result = _audit.domain_audit("export", batch_id, "system")
    cleanup_result = _cleanup.domain_cleanup(f"CLEANUP-{batch_id}", cleanup_module, 30)

    return {
        "chain": "delivery",
        "steps": {
            "store_result": store_result,
            "export": export_result,
            "audit": audit_result,
            "cleanup": cleanup_result,
        },
    }


# ---------------------------------------------------------------------------
# 调度领取链路：来源分片计划 → 调度批次凭据 → 建批/中断续领 → 批次追踪登记。
# ---------------------------------------------------------------------------
from app.services.dispatch_claim import (  # noqa: E402
    DispatchResponseInterrupted,
    claim_center as _claim_center,
)


def run_dispatch_claim(payload: dict[str, Any]) -> dict[str, Any]:
    """单次领取/续领/确认链路。

    payload 可带 op=claim|resume|confirm|board|history|range，缺省按参数推断。
    返回体同时平铺凭据关键字段并提供 steps，兼容不同断言习惯。
    """
    op = str(payload.get("op") or payload.get("action") or "claim").lower()
    if op in ("resume", "retry", "续领"):
        key = payload.get("application_id") or payload.get("credential_id") or payload.get("key")
        cred = _claim_center.resume_claim(key)
    elif op in ("confirm", "ack", "确认回执"):
        key = payload.get("application_id") or payload.get("credential_id") or payload.get("key")
        cred = _claim_center.confirm_claim(key)
    elif op in ("board", "overview"):
        board = _claim_center.source_board(str(payload.get("source", "SRC-PAGE")))
        return {"chain": "dispatch-claim", "steps": {"board": board}, "board": board}
    elif op in ("history",):
        events = _claim_center.claim_history(payload.get("application_id"))
        return {"chain": "dispatch-claim", "steps": {"history": events}, "history": events}
    elif op in ("range", "track"):
        tracked = _claim_center.get_batch_range(str(payload.get("key") or payload.get("batch_id")))
        return {"chain": "dispatch-claim", "steps": {"track": tracked}, "track": tracked, **(tracked or {})}
    else:
        try:
            cred = _claim_center.claim_shard(payload)
            interrupted = False
        except DispatchResponseInterrupted as exc:
            cred = exc.credential or {}
            interrupted = True
        tracked = _claim_center.get_batch_range(str(cred.get("credential_id"))) if cred else None
        return {
            "chain": "dispatch-claim",
            "interrupted": interrupted,
            **cred,
            "steps": {"claim": cred, "track_batch": tracked},
        }
    tracked = _claim_center.get_batch_range(str(cred.get("credential_id")))
    return {
        "chain": "dispatch-claim",
        **cred,
        "steps": {"claim": cred, "track_batch": tracked},
    }


def run_dispatch_scenario(payload: dict[str, Any]) -> dict[str, Any]:
    """完整复现题设：旧计划1-5第二片建批中断 → 新计划4-7先领 → 续回旧领取。"""
    source = str(payload.get("source") or "SRC-PAGE")
    scenario = _claim_center.load_scenario(source)
    new_claim = _claim_center.claim_shard(
        application_id=str(payload.get("new_application_id") or "APP-NEW-CHAIN"),
        source=source, plan_id=scenario["new_plan"]["plan_id"], shard_index=1,
    )
    resumed_old = _claim_center.resume_claim("APP-OLD-SHARD2")
    board = _claim_center.source_board(source)
    tracked_old = _claim_center.get_batch_range("APP-OLD-SHARD2")
    return {
        "chain": "dispatch-claim-scenario",
        "source": source,
        "steps": {
            "old_plan": scenario["old_plan"],
            "interrupted_old_claim": scenario["interrupted_credential"],
            "new_plan": scenario["new_plan"],
            "claim_new_plan": new_claim,
            "resume_old_claim": resumed_old,
            "board": board,
            "track_old": tracked_old,
        },
        "old_plan": scenario["old_plan"],
        "new_plan": scenario["new_plan"],
        "new_claim": new_claim,
        "resumed_old": resumed_old,
        "board": board,
        "track_old": tracked_old,
    }


@router.post("/dispatch-claim")
def dispatch_claim_chain(payload: dict[str, Any]) -> dict[str, Any]:
    return run_dispatch_claim(payload)


@router.post("/dispatch-scenario")
def dispatch_scenario_chain(payload: dict[str, Any]) -> dict[str, Any]:
    return run_dispatch_scenario(payload)