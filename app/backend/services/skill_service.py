"""技能记录服务 — 纯数据库操作"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.backend.models.skill_record import SkillRecord


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

    for key, value in kwargs.items():
        if hasattr(skill, key) and value is not None:
            setattr(skill, key, value)

    await db.flush()
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
