"""技能记录服务 — 纯数据库操作"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.backend.models.skill_record import SkillRecord

logger = logging.getLogger(__name__)


async def create_skill(
    db: AsyncSession,
    user_id: str,
    skill_name: str,
    proficiency: str | None = None,
    context: str | None = None,
    source: str = "form",
) -> SkillRecord:
    """创建技能记录"""
    skill = SkillRecord(
        user_id=user_id,
        skill_name=skill_name,
        proficiency=proficiency,
        context=context,
        source=source,
    )
    db.add(skill)
    await db.flush()

    # 写入成长事件
    try:
        from app.backend.services.growth_event_service import create_growth_event

        await create_growth_event(
            db=db,
            user_id=user_id,
            event_type="skill_added",
            entity_type="skill",
            entity_id=str(skill.id),
            payload={
                "skill_name": skill_name,
                "level": proficiency or "familiar",
                "source": source,
            },
            source="技能CRUD",
            project=True,
        )
    except Exception as e:
        logger.warning("创建技能成长事件失败: %s", e)

    return skill


async def get_user_skills(
    db: AsyncSession,
    user_id: str,
) -> list[SkillRecord]:
    """获取用户所有技能记录"""
    result = await db.execute(
        select(SkillRecord).where(SkillRecord.user_id == user_id).order_by(SkillRecord.created_at.desc())
    )
    return list(result.scalars().all())


async def update_skill(
    db: AsyncSession,
    skill_id: str,
    **kwargs,
) -> SkillRecord | None:
    """更新技能记录"""
    result = await db.execute(select(SkillRecord).where(SkillRecord.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        return None

    # 记录旧的熟练度
    old_proficiency = skill.proficiency

    for key, value in kwargs.items():
        if hasattr(skill, key) and value is not None:
            setattr(skill, key, value)

    await db.flush()

    # 如果熟练度变化，写入成长事件
    if "proficiency" in kwargs and kwargs["proficiency"] != old_proficiency:
        try:
            from app.backend.services.growth_event_service import create_growth_event

            await create_growth_event(
                db=db,
                user_id=skill.user_id,
                event_type="skill_level_changed",
                entity_type="skill",
                entity_id=str(skill.id),
                payload={
                    "skill_name": skill.skill_name,
                    "old_level": old_proficiency,
                    "new_level": kwargs["proficiency"],
                },
                source="技能CRUD",
                project=True,
            )
        except Exception as e:
            logger.warning("创建技能熟练度变化事件失败: %s", e)

    return skill


async def delete_skill(
    db: AsyncSession,
    skill_id: str,
    user_id: str,
) -> bool:
    """删除技能记录 — 校验所有权"""
    result = await db.execute(select(SkillRecord).where(SkillRecord.id == skill_id, SkillRecord.user_id == user_id))
    skill = result.scalar_one_or_none()
    if not skill:
        return False

    await db.delete(skill)
    await db.flush()
    return True
