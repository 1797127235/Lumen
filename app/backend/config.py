"""应用配置 - 从 .env 加载"""

from __future__ import annotations

from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parents[2] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 核心鉴权 ──
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    jwt_secret_key: str = ""  # 生产环境必须通过 .env 设置

    # ── 模型配置 ──
    embedding_model: str = "text-embedding-v4"

    # ── 前端 ──
    frontend_url: str = "http://localhost:5173"

    # ── 数据库 ──
    database_url: str = "sqlite+aiosqlite:///./career_os.db"

    # ── 应用 ──
    debug: bool = True
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]


@lru_cache()
def get_settings() -> Settings:
    return Settings()
