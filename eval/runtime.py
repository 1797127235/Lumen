"""BenchmarkRuntime — 为每个 benchmark case 初始化隔离的运行时。

每个 case 拥有：
  - 独立的 SQLite 数据库（文件级隔离）
  - 独立的 NullProvider（跳过 LanceDB，只用 FTS5）
  - 临时的 Agent system prompt 覆盖（注入 benchmark 指令）
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from core.db import Base, get_async_session_maker, get_engine, init_db
from core.migrations import migrate_sqlite
from lib.data_sources.ingestion.pipeline import init_pipeline
from lib.data_sources.ingestion.providers.null import NullProvider

logger = logging.getLogger(__name__)

# ── benchmark 专用 system prompt 后缀 ──────────────────────────────────────────

_BENCHMARK_SUFFIX = """
# Benchmark Mode

用中文回答。回答要简洁：一个词或一个短句，不要整段话。
不要问候语，不要反问，不要表情符号。

# 基于记忆回答（必须执行）

所有 benchmark 问题都可以从记忆中找到答案。假设答案存在于过去的对话中。
你的任务是检索它。不要轻易放弃。除非已经用 memory_search 穷举，否则不能说找不到。

第一步：每道题都必须先调用 memory_search，无一例外。
第二步：仔细阅读检索到的记忆内容。
第三步：你的回答必须基于检索结果，与之一致。
         - 不能给出忽略检索事实的泛泛回答。
         - 没有尝试 memory_search 之前不能说"不知道"或"找不到"。

不要向用户询问你可能已经存在记忆里的信息。
"""

_original_build_system_prompt: callable | None = None


# ── dataclass ────────────────────────────────────────────────────────────────


@dataclass
class BenchmarkRuntime:
    workspace: Path
    db_path: Path
    user_id: str = "bench_user"


# ── lifecycle ────────────────────────────────────────────────────────────────


async def create_runtime(workspace: Path) -> BenchmarkRuntime:
    """初始化一个完全隔离的 benchmark 运行时。

    每次调用会：
      1. 删除旧数据库（如存在）
      2. 重新 init_db + 建表 + migrate
      3. 注册 NullProvider（跳过语义搜索，只用 FTS5）
      4. 注册用户
      5. 注册工具
      6. 覆盖 Agent system prompt
    """
    workspace.mkdir(parents=True, exist_ok=True)
    db_path = workspace / "bench.db"

    # 1. 清理旧数据（DB + ingest 状态，确保每次 ingest 重新执行）
    if db_path.exists():
        db_path.unlink()
    ingest_state = workspace / "ingest_state.json"
    if ingest_state.exists():
        ingest_state.unlink()

    # 2. 初始化数据库（覆盖全局 engine / session_maker）
    # Windows 路径需用 as_posix() 避免反斜杠被当作转义字符
    db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    init_db(db_url)
    from lib import model_registry  # noqa: F401  -- 确保所有模型注册到 Base.metadata

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await migrate_sqlite(conn)

    # 3. 初始化 ingestion pipeline（NullProvider，不依赖 LanceDB）
    init_pipeline(workspace, NullProvider())

    # 4. 注册 benchmark 用户
    from lib.profile.models import User

    async with get_async_session_maker()() as db:
        db.add(User(user_id="bench_user", nickname="Bench User"))
        await db.commit()

    # 5. 注册工具
    from lib.tools.factory import register_all_tools

    register_all_tools()

    # 6. 覆盖 Agent system prompt
    _patch_agent()

    logger.info("BenchmarkRuntime ready: workspace=%s", workspace)
    return BenchmarkRuntime(workspace=workspace, db_path=db_path)


async def close_runtime(rt: BenchmarkRuntime) -> None:
    """释放资源并恢复全局状态。"""
    # 取消 ProjectionManager 后台任务（understanding 更新等）
    try:
        from lib.memory.projection import cancel_background_tasks

        cancel_background_tasks()
    except Exception as e:
        logger.warning("cancel background tasks failed: %s", e)

    # 给后台任务一点时间响应 cancel
    await asyncio.sleep(0.5)

    # 释放数据库连接池
    try:
        engine = get_engine()
        await engine.dispose()
    except Exception as e:
        logger.warning("engine dispose failed: %s", e)

    # 恢复 Agent system prompt
    _unpatch_agent()

    logger.info("BenchmarkRuntime closed: workspace=%s", rt.workspace)


# ── Agent patch ──────────────────────────────────────────────────────────────


def _patch_agent() -> None:
    """临时覆盖 LumenAgent.build_system_prompt，追加 benchmark 指令。"""
    global _original_build_system_prompt
    from core.agent import LumenAgent, _lumen_agent

    if _original_build_system_prompt is None:
        _original_build_system_prompt = LumenAgent.build_system_prompt

    def _patched(self: LumenAgent) -> str:
        base = _original_build_system_prompt(self)  # type: ignore[misc]
        return base + "\n\n" + _BENCHMARK_SUFFIX

    LumenAgent.build_system_prompt = _patched  # type: ignore[assignment]

    # 强制重建 Agent，使新 prompt 生效
    _lumen_agent._agent = None
    _lumen_agent._config_hash = ""


def _unpatch_agent() -> None:
    """恢复原始 build_system_prompt。"""
    global _original_build_system_prompt
    if _original_build_system_prompt is None:
        return

    from core.agent import LumenAgent, _lumen_agent

    LumenAgent.build_system_prompt = _original_build_system_prompt  # type: ignore[assignment]
    _original_build_system_prompt = None

    # 强制重建 Agent
    _lumen_agent._agent = None
    _lumen_agent._config_hash = ""
