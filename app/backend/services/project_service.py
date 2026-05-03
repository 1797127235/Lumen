"""项目经历服务 — 纯数据库操作"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.backend.models.project import Project


async def create_project(
    db: AsyncSession,
    user_id: str,
    title: str,
    tech_stack: str | None = None,
    role: str | None = None,
    period: str | None = None,
    description: str | None = None,
    source: str = "form",
) -> Project:
    """创建项目经历"""
    project = Project(
        user_id=user_id,
        title=title,
        tech_stack=tech_stack,
        role=role,
        period=period,
        description=description,
        source=source,
    )
    db.add(project)
    await db.flush()
    return project


async def get_user_projects(
    db: AsyncSession,
    user_id: str,
) -> list[Project]:
    """获取用户所有项目经历"""
    result = await db.execute(select(Project).where(Project.user_id == user_id).order_by(Project.created_at.desc()))
    return list(result.scalars().all())


async def update_project(
    db: AsyncSession,
    project_id: str,
    **kwargs,
) -> Project | None:
    """更新项目经历"""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        return None

    for key, value in kwargs.items():
        if hasattr(project, key) and value is not None:
            setattr(project, key, value)

    await db.flush()
    return project


async def delete_project(
    db: AsyncSession,
    project_id: str,
    user_id: str,
) -> bool:
    """删除项目经历 — 校验所有权"""
    result = await db.execute(select(Project).where(Project.id == project_id, Project.user_id == user_id))
    project = result.scalar_one_or_none()
    if not project:
        return False

    await db.delete(project)
    await db.flush()
    return True
