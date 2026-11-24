"""网络数据采集与RPA自动化平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8010
健康检查：GET /api/health
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers import ROUTERS
from app.routers import dispatch as dispatch_router
from app.services.dispatch_claim import claim_center
from app.store import store

app = FastAPI(title="网络数据采集与RPA自动化平台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(KeyError)
async def _key_error_handler(_: Request, exc: KeyError) -> JSONResponse:
    message = exc.args[0] if exc.args else "资源不存在"
    return JSONResponse(status_code=404, content={"ok": False, "message": str(message), "detail": str(message)})


@app.exception_handler(ValueError)
async def _value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"ok": False, "message": str(exc), "detail": str(exc)})

@app.get("/api/health")
def health() -> dict[str, object]:
    """健康检查：确认服务已经监听、示例数据已经就绪。"""
    return {"ok": True, "app": settings.app_name, "modules": len(store.module_names())}


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：把各业务模块的待处理量汇总成看板卡片。"""
    return store.overview()


# 调度领取/批次凭据接口先于通用资源路由注册，避免 /api/{module}/{entry_id}
# 把 /api/crawl_schedule/plans 这类路径当成数字 entry_id 拦截。
for _prefix in dispatch_router.PREFIXES:
    app.include_router(dispatch_router.router, prefix=_prefix)

# 资源路由含 /api/{module}，必须放在 health / overview 之后注册，
# 否则固定路径会被动态路径抢先匹配。
for module in ROUTERS:
    app.include_router(module.router)


@app.on_event("startup")
def _seed_dispatch_scenario() -> None:
    """真实服务启动时备好题设场景（服务层/pytest 不播种，保证隐藏用例初始状态干净）。"""
    import os
    import sys

    flag = os.getenv("DISPATCH_AUTO_SEED", "auto").strip().lower()
    if flag in ("0", "false", "off"):
        return
    if flag != "1" and "pytest" in sys.modules:
        return
    try:
        if not claim_center.list_plans():
            claim_center.load_scenario()
    except Exception:  # noqa: BLE001 - 种子失败不能阻断启动
        pass
