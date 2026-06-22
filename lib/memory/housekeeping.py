"""记忆定期整理 — 过期条目清理 + 时效性标记。

生命周期：
    1. startup.py 中创建并 start()
    2. 后台 asyncio task 循环运行
    3. shutdown 时 stop()

整理规则：
    - [intent] 标签超过 30 天 → 追加 ?stale 标记
    - [transient] 标签超过 7 天 → 删除
    - [fact] / [preference] → 永不过期
    - 无标签的旧条目 → 视为 fact（向后兼容）
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from datetime import UTC, datetime

from shared.logging import get_logger

logger = get_logger(__name__)

# ── 过期阈值 ──────────────────────────────────────────────────────

_INTENT_STALE_DAYS = 30
_TRANSIENT_EXPIRE_DAYS = 7
_RUN_INTERVAL_SECONDS = 86400  # 24 小时

# ── 条目解析 ──────────────────────────────────────────────────────

# 匹配: - 2026-06-03 — [category] content 或 - 2026-06-03 14:25 — [category] content
# 向后兼容:日期段可选地带时分(分钟)或时分秒
_ENTRY_RE = re.compile(r"^-\s+(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)\s+—\s+\[([^\]]+)\]\s+(.*)$")


def _parse_entry(line: str) -> tuple[str, str, str] | None:
    """解析记忆条目，返回 (date_str, category, content) 或 None。"""
    m = _ENTRY_RE.match(line.strip())
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def _is_stale_tag(category: str) -> bool:
    return category.endswith("?stale")


def _parse_entry_date(date_str: str) -> datetime | None:
    """解析条目时间戳，兼容旧日期格式和新时分格式。

    支持三种格式：
    - "2026-06-14"（旧）
    - "2026-06-14 23:05"（新，分钟粒度）
    - "2026-06-14 23:05:08"（新，秒级）

    解析失败返回 None，调用方应原样保留该行。
    """
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


# ── 整理逻辑 ──────────────────────────────────────────────────────


def housekeep_memory(content: str, now: datetime | None = None) -> tuple[str, int, int]:
    """整理 MEMORY.md 内容，返回 (new_content, removed_count, stale_marked_count)。

    纯文本操作，不依赖 LLM 调用。
    """
    if not content.strip():
        return content, 0, 0

    now = now or datetime.now(UTC)
    removed = 0
    stale_marked = 0

    lines = content.split("\n")
    result_lines: list[str] = []

    for line in lines:
        parsed = _parse_entry(line)
        if parsed is None:
            # 非条目行（标题、空行等）原样保留
            result_lines.append(line)
            continue

        date_str, category, text_content = parsed

        # 已经 stale 的 intent，再过 30 天删除
        if _is_stale_tag(category):
            entry_date = _parse_entry_date(date_str)
            if entry_date is None:
                result_lines.append(line)
                continue
            days_since = (now - entry_date).days
            if days_since > _INTENT_STALE_DAYS * 2:
                # stale 超过 60 天，删除
                removed += 1
                logger.debug("housekeep: removed stale intent", age_days=days_since, text=text_content[:50])
                continue
            result_lines.append(line)
            continue

        entry_date = _parse_entry_date(date_str)
        if entry_date is None:
            result_lines.append(line)
            continue

        days_since = (now - entry_date).days

        if category == "transient" and days_since > _TRANSIENT_EXPIRE_DAYS:
            removed += 1
            logger.debug("housekeep: removed expired transient", age_days=days_since, text=text_content[:50])
            continue

        if category == "intent" and days_since > _INTENT_STALE_DAYS:
            # 标记为 stale，下次再跑如果还 stale 就删除
            new_line = line.replace(f"[{category}]", f"[{category}?stale]")
            result_lines.append(new_line)
            stale_marked += 1
            logger.debug("housekeep: marked intent stale", age_days=days_since, text=text_content[:50])
            continue

        # fact, preference, correction, 自定义标签 → 保留
        result_lines.append(line)

    new_content = "\n".join(result_lines)
    return new_content, removed, stale_marked


# ── 后台定时任务 ──────────────────────────────────────────────────


class MemoryHousekeeper:
    """记忆定期整理器 — 后台定时清理过期条目。"""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("MemoryHousekeeper started", interval_h=_RUN_INTERVAL_SECONDS / 3600)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("MemoryHousekeeper stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._run_once()
            except Exception as e:
                logger.error("MemoryHousekeeper round failed: %s", e)
            await asyncio.sleep(_RUN_INTERVAL_SECONDS)

    async def _run_once(self) -> None:
        """扫描所有用户目录，整理过期记忆（MEMORY.md + USER.md）。"""
        from lib.agent.system_prompt_builder import invalidate_system_prompt_cache
        from lib.memory.markdown import _BASE_MEMORY_DIR, AsyncMarkdownStore

        store = AsyncMarkdownStore()

        # 找所有有记忆的用户目录
        memory_dir = _BASE_MEMORY_DIR
        if not memory_dir.exists():
            return

        total_removed = 0
        total_stale = 0

        for user_path in memory_dir.iterdir():
            if not user_path.is_dir():
                continue

            user_id = user_path.name

            for label, filename, read_fn, write_fn in [
                ("MEMORY.md", "MEMORY.md", store.read_memory, store.write_memory),
                ("USER.md", "USER.md", store.read_about_you, store.write_about_you),
            ]:
                if not (user_path / filename).exists():
                    continue
                try:
                    content = await read_fn(user_id)
                    if not content.strip():
                        continue
                    new_content, removed, stale = housekeep_memory(content)
                    if removed > 0 or stale > 0:
                        await write_fn(user_id, new_content)
                        invalidate_system_prompt_cache(user_id)
                        total_removed += removed
                        total_stale += stale
                        logger.info(
                            "housekeep: %s user=%s removed=%d stale=%d",
                            label,
                            user_id,
                            removed,
                            stale,
                        )
                except Exception as e:
                    logger.warning("housekeep: %s user=%s failed: %s", label, user_id, e)

        if total_removed or total_stale:
            logger.info(
                "housekeep round done: total_removed=%d total_stale=%d",
                total_removed,
                total_stale,
            )
