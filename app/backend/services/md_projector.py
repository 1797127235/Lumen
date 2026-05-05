"""MD Projector — 从 growth_events 投影到 .md 文件

职责：
- 从 growth_events 聚合生成 memory.md 和 entities/*.md
- 定义事件→画像合并规则
- 保证 .md 文件与 SQLite 真相源同步

合并规则：
- profile_updated: 字段级合并（最新值覆盖）
- skill_added/skill_level_changed: 按技能名合并（最新状态覆盖）
- experience_added: 追加（不去重）
- preference_learned: 最新值覆盖
- decision_made: 追加（保留时间线）
- status_changed: 最新值覆盖
- goal_updated: 最新值覆盖
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.models.growth_event import GrowthEvent
from app.backend.services.memory_service import ensure_memory_dirs

logger = logging.getLogger(__name__)


# ── 辅助函数 ─────────────────────────────────────────


def _deep_merge(base: dict, update: dict) -> dict:
    """递归深合并两个字典

    Args:
        base: 基础字典
        update: 更新字典

    Returns:
        合并后的字典
    """
    result = base.copy()
    for key, value in update.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # 递归合并嵌套字典
            result[key] = _deep_merge(result[key], value)
        else:
            # 直接覆盖
            result[key] = value
    return result


# ── 合并规则实现 ─────────────────────────────────────


def _merge_profile_events(events: list[GrowthEvent]) -> dict:
    """合并 profile_updated 事件（递归深合并，最新值覆盖）

    Args:
        events: profile_updated 事件列表

    Returns:
        合并后的 profile 字典
    """
    profile = {}
    for event in events:
        if not event.payload_json:
            continue
        try:
            payload = json.loads(event.payload_json)
            # 递归深合并，保留嵌套结构
            profile = _deep_merge(profile, payload)
        except json.JSONDecodeError:
            continue
    return profile


def _merge_skill_events(events: list[GrowthEvent]) -> dict:
    """合并技能事件（按技能名合并，最新状态覆盖）

    Args:
        events: skill_added/skill_level_changed 事件列表

    Returns:
        合并后的技能字典，key 为技能名
    """
    skills = {}
    for event in events:
        if not event.payload_json:
            continue
        try:
            payload = json.loads(event.payload_json)
            skill_name = payload.get("name") or payload.get("skill") or event.entity_id
            if skill_name:
                # 最新状态覆盖
                skills[skill_name] = {
                    "name": skill_name,
                    "level": payload.get("level", "familiar"),
                    "context": payload.get("context", ""),
                    "updated_at": event.created_at.isoformat() if event.created_at else None,
                }
        except json.JSONDecodeError:
            continue
    return skills


def _merge_experience_events(events: list[GrowthEvent]) -> list[dict]:
    """合并经历事件（追加，不去重）

    Args:
        events: experience_added 事件列表

    Returns:
        经历列表（按时间正序）
    """
    experiences = []
    for event in events:
        if not event.payload_json:
            continue
        try:
            payload = json.loads(event.payload_json)
            experiences.append(
                {
                    **payload,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                }
            )
        except json.JSONDecodeError:
            continue
    return experiences


def _merge_preference_events(events: list[GrowthEvent]) -> dict:
    """合并偏好事件（最新值覆盖）

    Args:
        events: preference_learned 事件列表

    Returns:
        合并后的偏好字典
    """
    preferences = {}
    for event in events:
        if not event.payload_json:
            continue
        try:
            payload = json.loads(event.payload_json)
            preferences.update(payload)
        except json.JSONDecodeError:
            continue
    return preferences


def _merge_decision_events(events: list[GrowthEvent]) -> list[dict]:
    """合并决策事件（追加，保留时间线）

    Args:
        events: decision_made 事件列表

    Returns:
        决策列表（按时间正序）
    """
    decisions = []
    for event in events:
        if not event.payload_json:
            continue
        try:
            payload = json.loads(event.payload_json)
            decisions.append(
                {
                    **payload,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                }
            )
        except json.JSONDecodeError:
            continue
    return decisions


def _merge_status_events(events: list[GrowthEvent]) -> dict:
    """合并状态事件（最新值覆盖）

    Args:
        events: status_changed 事件列表

    Returns:
        合并后的状态字典
    """
    status = {}
    for event in events:
        if not event.payload_json:
            continue
        try:
            payload = json.loads(event.payload_json)
            status.update(payload)
        except json.JSONDecodeError:
            continue
    return status


def _merge_goal_events(events: list[GrowthEvent]) -> dict:
    """合并目标事件（最新值覆盖）

    Args:
        events: goal_updated 事件列表

    Returns:
        合并后的目标字典
    """
    goals = {}
    for event in events:
        if not event.payload_json:
            continue
        try:
            payload = json.loads(event.payload_json)
            goals.update(payload)
        except json.JSONDecodeError:
            continue
    return goals


# ── .md 生成 ─────────────────────────────────────────


def _generate_memory_md(profile: dict, preferences: dict, status: dict, goals: dict) -> str:
    """生成 memory.md 内容

    Args:
        profile: 画像数据
        preferences: 偏好数据
        status: 状态数据
        goals: 目标数据

    Returns:
        memory.md 内容
    """
    date = datetime.now().strftime("%Y-%m-%d")
    parts = ["# 用户核心记忆", ""]
    parts.append("> 这个文件由 AI 自动管理，记录用户的核心信息。")
    parts.append("> 每次对话开始时会自动注入到 system prompt。")
    parts.append("")

    # 基础信息
    parts.append("## 基础信息")
    parts.append(f"- 学校：{profile.get('school_name', '（待填写）')}")
    parts.append(f"- 专业：{profile.get('major', '（待填写）')}")
    parts.append(f"- 年级：{profile.get('grade', '（待填写）')}")
    parts.append(f"- 毕业年份：{profile.get('graduation_year', '（待填写）')}")
    if profile.get("school_level"):
        parts.append(f"- 学校层次：{profile['school_level']}")
    parts.append("")

    # 目标方向
    parts.append("## 目标方向")
    parts.append(f"- 目标岗位：{profile.get('target_direction', goals.get('target_direction', '（待填写）'))}")
    parts.append(
        f"- 目标公司类型：{profile.get('target_company_level', goals.get('target_company_level', '（待填写）'))}"
    )
    parts.append(f"- 意向城市：{profile.get('city', goals.get('city', '（待填写）'))}")
    parts.append("")

    # 教育背景
    if profile.get("gpa") or profile.get("ranking") or profile.get("awards"):
        parts.append("## 教育背景")
        if profile.get("gpa"):
            parts.append(f"- GPA：{profile['gpa']}")
        if profile.get("ranking"):
            parts.append(f"- 排名：{profile['ranking']}")
        if profile.get("awards"):
            parts.append("- 获奖：")
            for award in profile["awards"]:
                parts.append(f"  - {award}")
        parts.append("")

    # 当前状态
    parts.append("## 当前状态")
    parts.append(f"- 正在学习：{status.get('learning', '（待填写）')}")
    parts.append(f"- 正在准备：{status.get('preparing', '（待填写）')}")
    parts.append(f"- 焦虑程度：{status.get('anxiety_level', '（待填写）')}")
    parts.append("")

    # 个人简介
    if profile.get("bio"):
        parts.append("## 个人简介")
        parts.append(profile["bio"])
        parts.append("")

    # 英语水平
    if profile.get("english_level"):
        parts.append("## 英语水平")
        parts.append(f"- {profile['english_level']}")
        parts.append("")

    # 期望薪资
    if profile.get("expected_salary"):
        parts.append("## 期望薪资")
        parts.append(f"- {profile['expected_salary']}")
        parts.append("")

    # 学习风格
    if preferences.get("learning_style"):
        parts.append("## 学习风格")
        parts.append(f"- 主要方式：{preferences['learning_style']}")
        parts.append("")

    parts.append(f"---\n*最后更新：{date}*")
    return "\n".join(parts)


def _generate_skills_md(skills: dict) -> str:
    """生成 entities/skills.md 内容

    Args:
        skills: 技能字典

    Returns:
        skills.md 内容
    """
    date = datetime.now().strftime("%Y-%m-%d")
    parts = ["# 技能列表", ""]
    parts.append("> 记录用户的技能状态，用于能力评估和学习建议。")
    parts.append("")

    if skills:
        parts.append("## 已掌握技能")
        for skill_name, skill_info in skills.items():
            parts.append(f"### {skill_name}")
            parts.append(f"- 状态：{skill_info.get('level', 'familiar')}")
            if skill_info.get("context"):
                parts.append(f"- 备注：{skill_info['context']}")
            parts.append("")

    parts.append(f"---\n*最后更新：{date}*")
    return "\n".join(parts)


def _generate_experiences_md(experiences: list[dict]) -> str:
    """生成 entities/experiences.md 内容

    Args:
        experiences: 经历列表

    Returns:
        experiences.md 内容
    """
    date = datetime.now().strftime("%Y-%m-%d")
    parts = ["# 经历列表", ""]
    parts.append("> 记录用户的项目经历、实习经历、获奖经历等。")
    parts.append("")

    if experiences:
        # 按类型分组
        projects = [e for e in experiences if e.get("type") == "project"]
        work = [e for e in experiences if e.get("type") == "work"]
        awards = [e for e in experiences if e.get("type") == "award"]

        if projects:
            parts.append("## 项目经历")
            for proj in projects:
                parts.append(f"### {proj.get('title', '未命名项目')}")
                if proj.get("period"):
                    parts.append(f"- 时间：{proj['period']}")
                if proj.get("tech_stack"):
                    parts.append(f"- 技术栈：{proj['tech_stack']}")
                if proj.get("role"):
                    parts.append(f"- 角色：{proj['role']}")
                if proj.get("description"):
                    parts.append(f"- 描述：{proj['description']}")
                parts.append("")

        if work:
            parts.append("## 实习经历")
            for w in work:
                parts.append(f"### {w.get('company', '未命名公司')} - {w.get('role', '未知岗位')}")
                if w.get("period"):
                    parts.append(f"- 时间：{w['period']}")
                if w.get("description"):
                    parts.append(f"- 描述：{w['description']}")
                parts.append("")

        if awards:
            parts.append("## 获奖经历")
            for award in awards:
                parts.append(f"### {award.get('name', '未命名奖项')}")
                if award.get("time"):
                    parts.append(f"- 时间：{award['time']}")
                if award.get("level"):
                    parts.append(f"- 级别：{award['level']}")
                parts.append("")

    parts.append(f"---\n*最后更新：{date}*")
    return "\n".join(parts)


def _generate_preferences_md(preferences: dict) -> str:
    """生成 entities/preferences.md 内容

    Args:
        preferences: 偏好字典

    Returns:
        preferences.md 内容
    """
    date = datetime.now().strftime("%Y-%m-%d")
    parts = ["# 偏好列表", ""]
    parts.append("> 记录用户的偏好和习惯，用于个性化建议。")
    parts.append("")

    parts.append("## 学习风格")
    parts.append(f"- 主要方式：{preferences.get('learning_style', '（待填写）')}")
    parts.append(f"- 辅助方式：{preferences.get('secondary_learning_style', '（待填写）')}")
    parts.append("")

    parts.append("## 交互偏好")
    parts.append(f"- 详细程度：{preferences.get('detail_level', '（待填写）')}")
    parts.append(f"- 是否喜欢代码示例：{preferences.get('like_code_examples', '（待填写）')}")
    parts.append(f"- 是否喜欢类比解释：{preferences.get('like_analogies', '（待填写）')}")
    parts.append("")

    parts.append(f"---\n*最后更新：{date}*")
    return "\n".join(parts)


def _generate_goals_md(goals: dict) -> str:
    """生成 entities/goals.md 内容

    Args:
        goals: 目标字典

    Returns:
        goals.md 内容
    """
    date = datetime.now().strftime("%Y-%m-%d")
    parts = ["# 目标列表", ""]
    parts.append("> 记录用户的短期和长期目标，用于规划和追踪。")
    parts.append("")

    parts.append("## 长期目标")
    if goals.get("long_term_goal"):
        parts.append(f"### {goals['long_term_goal']}")
        parts.append(f"- 时间范围：{goals.get('time_range', '（待填写）')}")
        parts.append(f"- 状态：{goals.get('status', '进行中')}")
    else:
        parts.append("### 找到理想工作")
        parts.append("- 时间范围：毕业前")
        parts.append("- 状态：进行中")
    parts.append("")

    parts.append(f"---\n*最后更新：{date}*")
    return "\n".join(parts)


def _generate_decisions_md(decisions: list[dict]) -> str:
    """生成 entities/decisions.md 内容

    Args:
        decisions: 决策列表

    Returns:
        decisions.md 内容
    """
    date = datetime.now().strftime("%Y-%m-%d")
    parts = ["# 决策记录", ""]
    parts.append("> 记录用户做出的重要决策，用于追踪决策过程和复盘。")
    parts.append("")

    if decisions:
        parts.append("## 决策历史")
        for decision in decisions:
            parts.append(f"### {decision.get('title', '未命名决策')}")
            if decision.get("created_at"):
                parts.append(f"- 时间：{decision['created_at']}")
            if decision.get("background"):
                parts.append(f"- 背景：{decision['background']}")
            if decision.get("decision"):
                parts.append(f"- 决策：{decision['decision']}")
            if decision.get("reason"):
                parts.append(f"- 理由：{decision['reason']}")
            parts.append("")

    parts.append(f"---\n*最后更新：{date}*")
    return "\n".join(parts)


# ── 核心投影函数 ─────────────────────────────────────


def _write_md_file_safe(path: str, content: str) -> None:
    """安全写入 .md 文件（先写临时文件，再 rename）

    Args:
        path: 目标文件路径
        content: 文件内容
    """
    import os
    import tempfile

    # 写入临时文件
    dir_name = os.path.dirname(path)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=dir_name, suffix=".tmp", delete=False) as f:
        f.write(content)
        temp_path = f.name

    # 原子 rename
    os.replace(temp_path, path)


async def project_user_to_md(db: AsyncSession, user_id: str) -> bool:
    """从 growth_events 全量重建用户的 .md 文件

    这是 .md 投影器的核心函数。它会：
    1. 读取用户的所有 growth_events
    2. 按事件类型聚合
    3. 应用合并规则
    4. 生成并写入 .md 文件（先写临时文件，再 rename）
    5. 标记事件已投影

    Args:
        db: 数据库会话
        user_id: 用户 ID

    Returns:
        是否成功
    """
    try:
        # 读取用户的所有事件（按时间正序）
        result = await db.execute(
            select(GrowthEvent).where(GrowthEvent.user_id == user_id).order_by(GrowthEvent.created_at.asc())
        )
        events = list(result.scalars().all())

        if not events:
            logger.debug("用户无事件，跳过投影: user_id=%s", user_id)
            return True

        # 按事件类型分组
        events_by_type = defaultdict(list)
        for event in events:
            events_by_type[event.event_type].append(event)

        # 应用合并规则
        profile = _merge_profile_events(events_by_type.get("profile_updated", []))
        skills = _merge_skill_events(
            events_by_type.get("skill_added", []) + events_by_type.get("skill_level_changed", [])
        )
        experiences = _merge_experience_events(events_by_type.get("experience_added", []))
        preferences = _merge_preference_events(events_by_type.get("preference_learned", []))
        decisions = _merge_decision_events(events_by_type.get("decision_made", []))
        status = _merge_status_events(events_by_type.get("status_changed", []))
        goals = _merge_goal_events(events_by_type.get("goal_updated", []))

        # 生成 .md 内容
        memory_md = _generate_memory_md(profile, preferences, status, goals)
        skills_md = _generate_skills_md(skills)
        experiences_md = _generate_experiences_md(experiences)
        preferences_md = _generate_preferences_md(preferences)
        goals_md = _generate_goals_md(goals)
        decisions_md = _generate_decisions_md(decisions)

        # 写入 .md 文件（先写临时文件，再 rename，确保原子性）
        from app.backend.config import USER_DATA_DIR

        ensure_memory_dirs()
        memory_dir = USER_DATA_DIR / "memory"
        entities_dir = memory_dir / "entities"

        _write_md_file_safe(str(memory_dir / "memory.md"), memory_md)
        _write_md_file_safe(str(entities_dir / "skills.md"), skills_md)
        _write_md_file_safe(str(entities_dir / "experiences.md"), experiences_md)
        _write_md_file_safe(str(entities_dir / "preferences.md"), preferences_md)
        _write_md_file_safe(str(entities_dir / "goals.md"), goals_md)
        _write_md_file_safe(str(entities_dir / "decisions.md"), decisions_md)

        # 标记所有事件已投影
        now = datetime.utcnow()
        for event in events:
            event.projected_md_at = now
        await db.flush()

        logger.info(
            ".md 投影完成: user_id=%s, events=%d, skills=%d, experiences=%d",
            user_id,
            len(events),
            len(skills),
            len(experiences),
        )
        return True

    except Exception as e:
        logger.error(".md 投影失败: user_id=%s, error=%s", user_id, e)
        return False


async def project_incremental_md(db: AsyncSession, user_id: str) -> bool:
    """增量投影：只处理未投影到 .md 的事件

    对于增量投影，我们重新全量重建 .md 文件，
    因为合并规则需要所有事件的上下文。

    Args:
        db: 数据库会话
        user_id: 用户 ID

    Returns:
        是否成功
    """
    # 增量投影实际上也是全量重建
    # 因为合并规则需要所有事件的上下文
    return await project_user_to_md(db, user_id)


async def create_event_and_project_md(
    db: AsyncSession,
    user_id: str,
    event_type: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict | None = None,
    source: str = "system",
) -> GrowthEvent | None:
    """统一写入入口：创建事件 + 同步投影 .md

    这是所有写入路径的统一入口。它会：
    1. 创建 growth_event（带去重）
    2. 同步投影到 .md 文件
    3. 返回创建的事件（如果去重则返回 None）

    Args:
        db: 数据库会话
        user_id: 用户 ID
        event_type: 事件类型
        entity_type: 实体类型（可选）
        entity_id: 实体 ID（可选）
        payload: 事件详情（可选）
        source: 事件来源

    Returns:
        创建的 GrowthEvent 实例，如果去重则返回 None
    """
    from app.backend.services.growth_event_service import create_growth_event_with_dedup

    # 创建事件（带去重）
    event = await create_growth_event_with_dedup(
        db=db,
        user_id=user_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
        source=source,
    )

    if event is None:
        # 去重跳过
        return None

    # 同步投影到 .md
    await project_user_to_md(db, user_id)

    return event
