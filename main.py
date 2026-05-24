"""Lumen 后端入口 — 仅做 FastAPI 装配，生命周期逻辑在 core.startup。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.config import get_settings
from core.startup import lifespan
from server.routes.chat import router as chat_router
from server.routes.config import router as config_router
from server.routes.health import router as health_router
from server.routes.mcp import router as mcp_router
from server.routes.memory import router as memory_router
from server.routes.notes import router as notes_router
from server.routes.partner import router as partner_router
from server.routes.providers import router as providers_router
from shared.logging import RequestLoggingMiddleware, get_logger

logger = get_logger(__name__)

app = FastAPI(
    title="Lumen",
    description="一个真正认识你的 AI 伙伴",
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
app.include_router(notes_router, prefix="/api")
app.include_router(providers_router, prefix="/api")
app.include_router(partner_router, prefix="/api")
app.include_router(mcp_router)


# ── 静态文件托管：dist/ 存在时始终挂载 ──
static_dir = Path(__file__).parent.parent / "dist"
if static_dir.exists():
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
