"""健康检查 — liveness + 就绪探测。

GET /api/health 做真实检查：DB 连通性 + 版本号统一（从 main.py 的 app 对象取，避免不一致）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from core.db import get_engine
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["health"])

# 版本号常量：main.py FastAPI() 的 version 与此保持一致
_APP_VERSION = "0.2.0"


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """健康检查：DB ping + 版本。

    返回 status="ok" 当 DB 可达，否则 status="degraded"。
    """
    db_ok = True
    db_error: str | None = None
    try:
        engine = get_engine()
        if engine is None:
            db_ok = False
            db_error = "engine 未初始化"
        else:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except Exception as exc:
        db_ok = False
        db_error = str(exc)[:200]
        logger.warning("health check DB ping 失败", error=db_error)

    return {
        "status": "ok" if db_ok else "degraded",
        "version": _APP_VERSION,
        "db": "ok" if db_ok else f"error: {db_error}",
    }
