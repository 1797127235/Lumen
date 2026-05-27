"""SQLAlchemy 引擎、声明基类与会话依赖注入"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from core.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_async_session_maker = None


def init_db(database_url: str | None = None, echo: bool | None = None):
    global _engine, _async_session_maker
    settings = get_settings()
    url = database_url or settings.database_url
    _engine = create_async_engine(url, echo=settings.debug if echo is None else echo)
    _async_session_maker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


def get_engine():
    return _engine


def get_async_session_maker() -> async_sessionmaker[AsyncSession]:
    assert _async_session_maker is not None, "init_db() 尚未调用"
    return _async_session_maker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    session_maker = get_async_session_maker()
    async with session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
