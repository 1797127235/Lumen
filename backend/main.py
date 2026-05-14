"""Lumen 后端入口 — 仅做 FastAPI 装配，生命周期逻辑在 core.startup。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.core.config import get_settings
from backend.core.logging import RequestLoggingMiddleware, get_logger
from backend.core.startup import lifespan
from backend.modules.chat.router import router as chat_router
from backend.modules.config.router import router as config_router
from backend.modules.data_sources.router import router as data_sources_router
from backend.modules.health.router import router as health_router
from backend.modules.memory.router import router as memory_router

logger = get_logger(__name__)

app = FastAPI(
    title="Lumen",
    description="一个真正认识你的 AI 伴侣",
    version="0.2.0",
    lifespan=lifespan,
)

# ── CORS：开发模式允许跨域（前端 :5173），生产模式不需（单端口）──
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"] if _settings.debug else ["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"] if _settings.debug else ["Authorization", "Content-Type"],
)

# ── 请求日志中间件 ──
app.add_middleware(RequestLoggingMiddleware)

# ── API 路由 ──
app.include_router(health_router, prefix="/api")
app.include_router(memory_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(data_sources_router, prefix="/api")

# ── 静态文件托管：dist/ 存在时始终挂载 ──
static_dir = Path(__file__).parent.parent / "dist"
if static_dir.exists():

    @app.get("/api/{path:path}")
    async def api_not_found(path: str):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "Not Found"}, status_code=404)

    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
