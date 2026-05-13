"""Lumen 后端入口"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api import chat
from backend.api.routers import config, health, memory
from backend.config import apply_user_config, get_settings
from backend.data_sources.routes import router as data_sources_router
from backend.db import Base, get_engine, init_db
from backend.db_migrations import migrate_sqlite
from backend.domain.models import *  # noqa — 确保所有模型注册到 Base
from backend.logging_config import RequestLoggingMiddleware, get_logger, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库表 + SQLite 兼容迁移 + 加载用户配置"""
    settings = get_settings()

    # 初始化日志系统（生产环境 JSON，开发环境彩色控制台）
    setup_logging(json_logs=not settings.debug, log_level="DEBUG" if settings.debug else "INFO")

    init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if "sqlite" in str(engine.url):
            await migrate_sqlite(conn)
    applied = apply_user_config(settings)
    if applied:
        logger.info("config.json 覆盖", keys=list(applied.keys()))
    # Cognee 记忆层初始化（后台线程）+ cognify 定时循环（async task）
    import asyncio
    import threading

    from backend.memory.cognify_loop import cognify_loop, init_cognee

    threading.Thread(target=init_cognee, daemon=True, name="cognee-init").start()
    _cognee_tasks: list[asyncio.Task] = []
    _cognee_tasks.append(asyncio.create_task(cognify_loop(), name="cognee-cognify-loop"))

    # ── 外部数据摄入（始终初始化，支持 API 创建的数据源）──
    from backend.config import USER_DATA_DIR
    from backend.ingestion import init_pipeline

    store_dir = USER_DATA_DIR
    store_dir.mkdir(exist_ok=True)
    pipeline = init_pipeline(store_dir)

    async def _bootstrap_ingestion() -> None:
        """等待 DB 就绪后，加载 data_sources 并启动扫描/监听。"""
        try:
            await asyncio.sleep(2)  # 等待 DB 完全初始化
            from sqlalchemy import select

            from backend.data_sources.models import DataSource
            from backend.data_sources.registry import create_connector
            from backend.data_sources.service import create_data_source
            from backend.db import get_async_session_maker

            async with get_async_session_maker()() as db:
                # 查询 active data_sources
                result = await db.execute(select(DataSource).where(DataSource.status == "active"))
                sources = list(result.scalars().all())

                # 如果 DB 为空但 .env 有 EXTERNAL_DATA_DIRS，自动创建（向后兼容）
                if not sources and settings.external_data_dir_list:
                    dirs = settings.external_data_dir_list
                    ds = await create_data_source(
                        db,
                        user_id="demo_user",
                        name="本地文件夹",
                        type="local_folder",
                        config={"paths": dirs},
                    )
                    sources = [ds]

                # 注册连接器
                for ds in sources:
                    connector = create_connector(ds)
                    if connector:
                        pipeline.register(connector)

            # 后台全量扫描
            summary = await pipeline.run_full_scan()
            logger.info("ingestion.initial_scan_complete", summary=summary)

            # 启动文件监听
            pipeline.start_watching_all()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("ingestion.bootstrap_failed", error=str(exc))

    _ingestion_task: asyncio.Task | None = asyncio.create_task(
        _bootstrap_ingestion(), name="external-ingestion-bootstrap"
    )

    yield

    # 关闭时取消未完成的 Cognee 投影任务
    from backend.memory import cancel_background_tasks

    cancel_background_tasks()

    # 清理外部数据摄入
    import contextlib

    from backend.ingestion import get_pipeline

    with contextlib.suppress(AssertionError):
        get_pipeline().stop_watching_all()
    if _ingestion_task and not _ingestion_task.done():
        _ingestion_task.cancel()

    await engine.dispose()


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

app.include_router(health.router, prefix="/api")
app.include_router(memory.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(config.router, prefix="/api")
app.include_router(data_sources_router, prefix="/api")

# ── 静态文件托管：dist/ 存在时始终挂载（开发/桌面/生产都可用） ──
if True:  # 始终启用（桌面/生产模式依赖此挂载）
    from pathlib import Path

    static_dir = Path(__file__).parent.parent / "frontend" / "dist"
    if static_dir.exists():
        # API 404 兜底：避免未匹配的 /api/* 被静态文件拦截
        @app.get("/api/{path:path}")
        async def api_not_found(path: str):
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "Not Found"}, status_code=404)

        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
