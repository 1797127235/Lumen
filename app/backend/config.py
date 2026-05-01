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
    jwt_secret_key: str = "career-planner-secret-key-2024"

    # ── 外部 API ──
    tavily_api_key: str = ""
    firecrawl_api_key: str = ""

    # ── 简历解析 SDK ──
    resumesdk_appcode: str = ""
    resumesdk_appkey: str = ""
    resumesdk_appsecret: str = ""

    # ── 讯飞（语音） ──
    xfyun_app_id: str = ""
    xfyun_api_key: str = ""
    xfyun_api_secret: str = ""

    # ── 模型配置 ──
    embedding_model: str = "text-embedding-v4"
    # LLM_MODEL 不在此设 — 代码内置 qwen-plus / qwen-max 按用途路由

    # ── PDF 导出 ──
    pdf_export_engine: str = "playwright"
    pdf_export_timeout_seconds: int = 60
    pdf_export_page_format: str = "A4"
    pdf_export_locale: str = "zh-CN"
    pdf_export_fallback_to_reportlab: bool = True

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
