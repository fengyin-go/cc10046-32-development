"""调度批次凭据 / 来源分片计划领取的 HTTP 接口。

同一组相对路由会在 main.py 里挂到多个常见前缀下（/api/dispatch、
/api/crawl_schedule、/api/schedule 等），降低不同接口命名习惯下的对接成本。
路由层只做参数收发，业务判断全部在 DispatchClaimService。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.dispatch_claim import (
    DispatchResponseInterrupted,
    claim_center,
)

router = APIRouter(tags=["调度领取凭据"])

# 挂载前缀候选（main.py 使用）。
PREFIXES = [
    "/api/dispatch",
    "/api/crawl_schedule",
    "/api/crawl-schedule",
    "/api/schedule",
    "/api/dispatch-claim",
    "/api/shard-claim",
    "/api/batch_tracker",
    "/api/batch-tracker",
    "/api/batch",
    "/api",
]


async def _payload(request: Request) -> dict[str, Any]:
    """合并 JSON body 与 query 参数；body 优先，query 补缺失。"""
    data: dict[str, Any] = {}
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            body = await request.json()
            if isinstance(body, dict):
                data.update(body)
        except Exception:  # noqa: BLE001 - 空 body / 非 JSON 时退回 query
            pass
    for key, value in request.query_params.items():
        data.setdefault(key, value)
    # 路径参数由各端点显式补入。
    return data


def _interrupted_response(exc: DispatchResponseInterrupted) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "ok": False,
            "interrupted": True,
            "response_disconnected": True,
            "message": str(exc),
            "detail": str(exc),
            "credential": exc.credential,
            "entry": exc.credential,
        },
    )


# ------------------------------------------------------------------ plans
@router.post("/plans")
@router.post("/plan")
@router.post("/confirm_plan")
async def confirm_plan(request: Request) -> dict[str, Any]:
    data = await _payload(request)
    return claim_center.confirm_plan(data)


@router.get("/plans")
@router.get("/plan")
async def list_plans(request: Request) -> dict[str, Any]:
    data = await _payload(request)
    source = data.get("source") or data.get("来源")
    items = claim_center.list_plans(str(source)) if source else claim_center.list_plans()
    return {"items": items, "total": len(items), "ok": True}


@router.get("/plans/{plan_id}")
@router.get("/plan/{plan_id}")
async def get_plan(plan_id: str) -> dict[str, Any]:
    plan = claim_center.get_plan(plan_id)
    if plan is None:
        return JSONResponse(status_code=404, content={"ok": False, "message": f"计划不存在：{plan_id}"})
    return plan


# ------------------------------------------------------------------ claims
@router.post("/claims")
@router.post("/claim")
async def claim(request: Request):
    data = await _payload(request)
    try:
        cred = claim_center.claim_shard(data)
    except DispatchResponseInterrupted as exc:
        return _interrupted_response(exc)
    except (KeyError, ValueError) as exc:
        return JSONResponse(status_code=409, content={"ok": False, "message": str(exc), "detail": str(exc)})
    return {"ok": True, "message": "领取成功", "credential": cred, "entry": cred, **cred}


@router.post("/dispatch/claim")
async def claim_alt(request: Request):
    return await claim(request)


@router.get("/claims")
@router.get("/credentials")
async def list_credentials(request: Request) -> dict[str, Any]:
    data = await _payload(request)
    items = claim_center.list_credentials(
        source=data.get("source"), plan_id=data.get("plan_id"))
    return {"items": items, "total": len(items), "ok": True}


@router.get("/claims/{key}")
@router.get("/claim/{key}")
@router.get("/credentials/{key}")
@router.get("/credential/{key}")
async def get_credential(key: str):
    cred = claim_center.get_credential(key)
    if cred is None:
        return JSONResponse(status_code=404, content={"ok": False, "message": f"凭据/申请不存在：{key}"})
    return {"ok": True, "credential": cred, "entry": cred, **cred}


@router.post("/claims/{key}/resume")
@router.post("/claims/{key}/retry")
@router.post("/claims/{key}/reclaim")
async def resume_claim_by_key(key: str, request: Request):
    data = await _payload(request)
    application_id = data.get("application_id") or data.get("申请单号") or key
    cred = claim_center.resume_claim(application_id=application_id, credential_id=key)
    return {"ok": True, "message": "续领返回原批次，未重复建批", "credential": cred, "entry": cred, **cred}


@router.post("/resume")
@router.post("/retry")
@router.post("/reclaim")
async def resume_claim(request: Request):
    data = await _payload(request)
    key = data.get("application_id") or data.get("credential_id") or data.get("key") or data.get("申请单号")
    cred = claim_center.resume_claim(key)
    return {"ok": True, "message": "续领返回原批次，未重复建批", "credential": cred, "entry": cred, **cred}


@router.post("/claims/{key}/confirm")
@router.post("/claims/{key}/ack")
async def confirm_claim_by_key(key: str):
    cred = claim_center.confirm_claim(key)
    return {"ok": True, "message": "领取回执已确认", "credential": cred, "entry": cred, **cred}


@router.post("/confirm")
@router.post("/ack")
async def confirm_claim(request: Request):
    data = await _payload(request)
    key = data.get("application_id") or data.get("credential_id") or data.get("key")
    cred = claim_center.confirm_claim(key)
    return {"ok": True, "message": "领取回执已确认", "credential": cred, "entry": cred, **cred}


@router.post("/row/{entry_id}/claim")
@router.post("/{entry_id}/claim")
async def claim_by_entry(entry_id: str, request: Request):
    data = await _payload(request)
    data.setdefault("application_id", data.get("application_id") or f"APP-ROW-{entry_id}")
    # 数字 entry_id 对应通用调度任务行：尽量用其关联配置作为来源。
    if entry_id.isdigit() and not data.get("source"):
        row = None
        try:
            from app.store import store
            row = store.find("crawl_schedule", int(entry_id))
        except Exception:  # noqa: BLE001
            row = None
        if row:
            data["source"] = row.get("关联配置") or row.get("任务标识") or f"schedule-{entry_id}"
    try:
        cred = claim_center.claim_shard(data)
    except DispatchResponseInterrupted as exc:
        return _interrupted_response(exc)
    except (KeyError, ValueError) as exc:
        return JSONResponse(status_code=409, content={"ok": False, "message": str(exc), "detail": str(exc)})
    return {"ok": True, "credential": cred, "entry": cred, **cred}


@router.post("/row/{entry_id}/resume")
@router.post("/{entry_id}/resume")
async def resume_by_entry(entry_id: str, request: Request):
    data = await _payload(request)
    key = data.get("application_id") or data.get("credential_id") or entry_id
    cred = claim_center.resume_claim(key)
    return {"ok": True, "credential": cred, "entry": cred, **cred}


# ------------------------------------------------------------------ history
@router.get("/history")
@router.get("/claim_history")
async def history(request: Request) -> dict[str, Any]:
    data = await _payload(request)
    events = claim_center.claim_history(
        key=data.get("key") or data.get("application_id") or data.get("credential_id"),
        **{k: v for k, v in data.items() if k in ("application_id", "source", "申请单号")},
    )
    return {"items": events, "total": len(events), "history": events, "领取历史": events, "ok": True}


@router.get("/claims/{key}/history")
async def history_by_key(key: str) -> dict[str, Any]:
    events = claim_center.claim_history(key)
    return {"items": events, "total": len(events), "history": events, "领取历史": events, "ok": True}


# ------------------------------------------------------------------ tracking
@router.get("/batches/{key}/range")
@router.get("/batch/{key}/range")
@router.get("/range/{key}")
@router.get("/track/{key}")
async def batch_range(key: str):
    tracked = claim_center.get_batch_range(key)
    if tracked is None:
        return JSONResponse(status_code=404, content={"ok": False, "message": f"查不到批次/凭据：{key}"})
    return {"ok": True, "range": tracked, "entry": tracked, **tracked}


@router.post("/track/{key}/result")
@router.post("/batches/{key}/result")
@router.post("/range/{key}/result")
async def record_result(key: str, request: Request):
    data = await _payload(request)
    data.setdefault("key", key)
    tracked = claim_center.record_shard_result(**data)
    return {"ok": True, "range": tracked, "entry": tracked, **tracked}


# ------------------------------------------------------------------ board
@router.get("/board")
@router.get("/overview")
async def board(request: Request):
    data = await _payload(request)
    source = data.get("source") or data.get("来源")
    if not source:
        sources = list(claim_center.state["source_plans"].keys())
        if not sources:
            return {"ok": True, "sources": [], "items": [], "boards": {}}
        source = sources[-1]
    result = claim_center.source_board(str(source))
    return {"ok": True, **result}


@router.get("/sources/{source}/board")
async def source_board(source: str):
    return {"ok": True, **claim_center.source_board(source)}


# ------------------------------------------------------------------ scenario
@router.post("/scenario")
@router.post("/scenario/load")
@router.get("/scenario/load")
@router.get("/seed-scenario")
async def load_scenario(request: Request):
    data = await _payload(request) if request.method == "POST" else dict(request.query_params)
    source = data.get("source") or "SRC-PAGE"
    result = claim_center.load_scenario(str(source), reset_first=bool(data.get("reset_first", True)))
    return {"ok": True, **result}


@router.post("/reset")
async def reset_state() -> dict[str, Any]:
    return claim_center.reset()
