"""将已提交的成长事件投影为 Markdown 快照。

从 services/md_projector.py + services/memory_service.py + services/memory_limits.py + services/memory_templates.py 迁移。
纯事件合并与格式化逻辑已提取到 events_merger.py，本文件仅保留文件 I/O 与投影调度。
"""

from __future__ import annotations

import os
import tempfile
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.config import USER_DATA_DIR
from app.backend.db.base import get_async_session_maker
from app.backend.logging_config import get_logger
from app.backend.memory.constants import MD_CHAR_LIMITS
from app.backend.memory.projections.events_merger import (
    generate_experiences_md,
    generate_memory_md,
    generate_skills_md,
    merge_decision_events,
    merge_dict_events,
    merge_experience_events,
    merge_profile_events,
    merge_skill_events,
)
from app.backend.models.growth_event import GrowthEvent

logger = get_logger(__name__)

MEMORY_CHAR_LIMIT = MD_CHAR_LIMITS["memory"]
SKILLS_CHAR_LIMIT = MD_CHAR_LIMITS["skills"]
EXPERIENCES_CHAR_LIMIT = MD_CHAR_LIMITS["experiences"]

_BASE_MEMORY_DIR = USER_DATA_DIR / "memory"


def memory_dir(user_id: str) -> Path:
    """返回 user_id 对应的记忆目录路径。"""
    safe_id = Path(user_id).name
    return _BASE_MEMORY_DIR / safe_id


def ensure_memory_dirs(user_id: str) -> None:
    memory_dir(user_id).mkdir(parents=True, exist_ok=True)


# ── 模板 ──


def memory_default() -> str:
    return """# 用户核心记忆

> 这个文件由 AI 自动管理，记录用户的核心信息。

## 基础信息
- 学校：（待填写）
- 专业：（待填写）
- 年级：（待填写）
- 毕业年份：（待填写）

## 目标方向
- 目标岗位：（待填写）
- 目标公司类型：（待填写）
- 意向城市：（待填写）

## 当前状态
- 正在学习：（待填写）
- 正在准备：（待填写）
- 焦虑程度：（待填写）

---
*最后更新：待填写*
"""


def skills_default() -> str:
    return """# 技能列表

> 记录用户的技能状态，用于能力评估和学习建议。

---
*最后更新：待填写*
"""


def experiences_default() -> str:
    return """# 经历列表

> 记录用户的项目、实习、竞赛和其它成长经历。

---
*最后更新：待填写*
"""


# ── .md 文件读写 ──


def read_memory(user_id: str) -> str:
    memory_file = memory_dir(user_id) / "memory.md"
    if not memory_file.exists():
        return ""
    return memory_file.read_text(encoding="utf-8")


def write_memory(user_id: str, content: str) -> None:
    ensure_memory_dirs(user_id)
    (memory_dir(user_id) / "memory.md").write_text(content, encoding="utf-8")


def read_skills(user_id: str) -> str:
    skills_file = memory_dir(user_id) / "skills.md"
    if not skills_file.exists():
        return ""
    return skills_file.read_text(encoding="utf-8")


def write_skills(user_id: str, content: str) -> None:
    ensure_memory_dirs(user_id)
    (memory_dir(user_id) / "skills.md").write_text(content, encoding="utf-8")


def read_experiences(user_id: str) -> str:
    exp_file = memory_dir(user_id) / "experiences.md"
    if not exp_file.exists():
        return ""
    return exp_file.read_text(encoding="utf-8")


def write_experiences(user_id: str, content: str) -> None:
    ensure_memory_dirs(user_id)
    (memory_dir(user_id) / "experiences.md").write_text(content, encoding="utf-8")


# ── 文件 I/O 辅助 ──


def _truncate_to_limit(content: str, limit: int, *, keep_tail: bool = False) -> str:
    if len(content) <= limit:
        return content
    if keep_tail:
        truncated = content[-limit:]
        first_newline = truncated.find("\n\n")
        if first_newline >= 0:
            truncated = truncated[first_newline + 2 :]
    else:
        truncated = content[:limit]
        last_newline = truncated.rfind("\n\n")
        if last_newline > 0:
            truncated = truncated[:last_newline]
    logger.warning("Content truncated", orig=len(content), truncated=len(truncated), keep_tail=keep_tail)
    return truncated


def _write_md_file_safe(path: str, content: str, max_chars: int | None = None, *, keep_tail: bool = False) -> None:
    if max_chars is not None:
        content = _truncate_to_limit(content, max_chars, keep_tail=keep_tail)
    dir_name = os.path.dirname(path)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=dir_name,
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(content)
        temp_path = handle.name
    os.replace(temp_path, path)


def _write_default_md_snapshot(user_id: str) -> None:
    ensure_memory_dirs(user_id)
    d = memory_dir(user_id)
    _write_md_file_safe(str(d / "memory.md"), memory_default())
    _write_md_file_safe(str(d / "skills.md"), skills_default())
    _write_md_file_safe(str(d / "experiences.md"), experiences_default())


# ── 主投影函数 ──


async def project_user_to_md(db: AsyncSession, user_id: str) -> bool:
    """从 GrowthEvent 全量重建 .md 文件。"""
    try:
        result = await db.execute(
            select(GrowthEvent).where(GrowthEvent.user_id == user_id).order_by(GrowthEvent.created_at.asc())
        )
        events = list(result.scalars().all())

        if not events:
            _write_default_md_snapshot(user_id)
            logger.debug("No events found; rebuilt default markdown", user_id=user_id)
            return True

        events_by_type: dict[str, list[GrowthEvent]] = defaultdict(list)
        for event in events:
            events_by_type[event.event_type].append(event)

        profile = merge_profile_events(events_by_type.get("profile_updated", []))
        skills = merge_skill_events(
            events_by_type.get("skill_added", []) + events_by_type.get("skill_level_changed", [])
        )
        experiences = merge_experience_events(events_by_type.get("experience_added", []))
        preferences = merge_dict_events(events_by_type.get("preference_learned", []))
        status = merge_dict_events(events_by_type.get("status_changed", []))
        goals = merge_dict_events(events_by_type.get("goal_updated", []))
        decisions = merge_decision_events(events_by_type.get("decision_made", []))

        ensure_memory_dirs(user_id)
        d = memory_dir(user_id)

        _write_md_file_safe(
            str(d / "memory.md"),
            generate_memory_md(profile, preferences, status, goals, decisions),
            max_chars=MEMORY_CHAR_LIMIT,
        )
        _write_md_file_safe(
            str(d / "skills.md"), generate_skills_md(skills), max_chars=SKILLS_CHAR_LIMIT, keep_tail=True
        )
        _write_md_file_safe(
            str(d / "experiences.md"),
            generate_experiences_md(experiences),
            max_chars=EXPERIENCES_CHAR_LIMIT,
            keep_tail=True,
        )

        # 清理旧的 entities 目录
        entities_dir = d / "entities"
        if entities_dir.exists():
            import shutil as _shutil

            _shutil.rmtree(entities_dir, ignore_errors=True)

        from datetime import UTC, datetime

        now = datetime.now(UTC)
        for event in events:
            event.projected_md_at = now
        await db.flush()

        logger.info(
            ".md projection complete",
            user_id=user_id,
            events=len(events),
            skills=len(skills),
            experiences=len(experiences),
        )
        return True
    except Exception as exc:
        logger.error(".md projection failed", user_id=user_id, error=str(exc))
        return False


async def sync_user_md_projection(user_id: str) -> bool:
    """同步 .md 文件：只重建有未投影事件的用户。"""
    async with get_async_session_maker()() as db:
        from sqlalchemy import func as _func

        dirty = await db.execute(
            select(_func.count(GrowthEvent.id)).where(
                GrowthEvent.user_id == user_id,
                GrowthEvent.projected_md_at.is_(None),
            )
        )
        if (dirty.scalar() or 0) == 0:
            return True

        success = await project_user_to_md(db, user_id)
        if success:
            await db.commit()
        else:
            await db.rollback()
        return success
