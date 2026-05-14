"""应用生命周期 — 启动/关闭编排。"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select

from backend.core.config import USER_DATA_DIR, apply_user_config, get_settings
from backend.core.db import Base, get_async_session_maker, get_engine, init_db
from backend.core.logging import get_logger, setup_logging
from backend.core.migrations import migrate_sqlite
from backend.modules.data_sources.ingestion import get_pipeline, init_pipeline
from backend.modules.data_sources.models import DataSource
from backend.modules.data_sources.registry import create_connector

logger = get_logger(__name__)


def _init_logging() -> None:
    settings = get_settings()
    setup_logging(
        json_logs=not settings.debug,
        log_level="DEBUG" if settings.debug else "INFO",
    )


async def _init_db() -> None:
    # 确保所有模型注册到 Base.metadata（自包含，不依赖 main.py 底部 import）
    from backend import model_registry  # noqa: F401

    init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if "sqlite" in str(engine.url):
            await migrate_sqlite(conn)


def _init_cognee() -> list[asyncio.Task]:
    from backend.modules.memory.cognify_loop import cognify_loop, init_cognee

    threading.Thread(target=init_cognee, daemon=True, name="cognee-init").start()
    tasks: list[asyncio.Task] = [
        asyncio.create_task(cognify_loop(), name="cognee-cognify-loop"),
    ]
    return tasks


async def _bootstrap_ingestion() -> None:
    """等待 DB 就绪后，加载 data_sources 并启动扫描/监听。

    仅当用户通过 UI/API 显式配置了 data_sources 时才启动，
    不再从 .env 的 EXTERNAL_DATA_DIRS 自动创建（避免 unsolicited ingestion）。
    """
    try:
        await asyncio.sleep(2)
        store_dir = USER_DATA_DIR
        store_dir.mkdir(exist_ok=True)
        pipeline = init_pipeline(store_dir)
        await pipeline.start()

        async with get_async_session_maker()() as db:
            result = await db.execute(select(DataSource).where(DataSource.status == "active"))
            sources = list(result.scalars().all())

            for ds in sources:
                connector = create_connector(ds)
                if connector:
                    pipeline.register(connector)

        if not pipeline._connectors:
            logger.info("ingestion.no_sources_configured", skip=True)
            return

        # Phase 1: 自动迁移旧 JSON 状态到 DB（幂等）
        try:
            migrate_stats = await pipeline._store.migrate_from_json()
            if migrate_stats["migrated"] > 0:
                logger.info("ingestion.migrated_from_json", stats=migrate_stats)
        except Exception as exc:
            logger.warning("ingestion.migrate_failed", error=str(exc))

        summary = await pipeline.run_full_scan()
        logger.info("ingestion.initial_scan_complete", summary=summary)
        pipeline.start_watching_all()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("ingestion.bootstrap_failed", error=str(exc))


async def _shutdown(
    engine,
    cognee_tasks: list[asyncio.Task],
    ingestion_task: asyncio.Task | None,
) -> None:
    from backend.modules.memory import cancel_background_tasks

    cancel_background_tasks()

    with contextlib.suppress(AssertionError):
        get_pipeline().stop_watching_all()

    with contextlib.suppress(AssertionError):
        await get_pipeline().stop()

    if ingestion_task and not ingestion_task.done():
        ingestion_task.cancel()

    for task in cognee_tasks:
        if not task.done():
            task.cancel()

    await engine.dispose()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan — 启动初始化 + 关闭清理。"""
    _init_logging()

    await _init_db()

    applied = apply_user_config(get_settings())
    if applied:
        logger.info("config.json 覆盖", keys=list(applied.keys()))

    cognee_tasks = _init_cognee()

    ingestion_task: asyncio.Task | None = asyncio.create_task(
        _bootstrap_ingestion(), name="external-ingestion-bootstrap"
    )

    yield

    await _shutdown(get_engine(), cognee_tasks, ingestion_task)
