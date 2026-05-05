"""应用配置 — 环境变量 + config.json 双层配置

优先级: config.json > 环境变量 (.env) > 默认值
USER_DATA_DIR: ~/.careeros/（用户运行时数据，跨版本持久化）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

# ── 目录常量 ────────────────────────────────────────

# 用户运行时数据目录（SQLite / Chroma / config.json）
USER_DATA_DIR = Path.home() / ".careeros"


def _ensure_user_data_dir() -> None:
    """确保用户数据目录存在"""
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── Settings ─────────────────────────────────────────


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parents[2] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM Provider ──
    llm_provider: str = "dashscope"
    llm_model: str = "qwen-plus"
    llm_api_key: str = ""
    llm_base_url: str = ""  # 空 = 使用 LiteLLM 默认值

    # ── Embedding Provider ──
    embedding_provider: str = "dashscope"
    embedding_model: str = "text-embedding-v4"
    embedding_api_key: str = ""  # 空 = 使用 llm_api_key
    embedding_base_url: str = ""

    # ── 旧字段（保留，不参与 fallback）──
    dashscope_api_key: str = ""

    # ── 数据库 ──
    database_url: str = ""

    # ── Cognee ──
    # 单实例 / 自托管默认共用一个数据集；多用户同机部署时请为每用户拆分策略另行设计
    cognee_dataset: str = "career_os"

    # ── 应用 ──
    debug: bool = True
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:5174", "http://localhost:3000"]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # 数据库默认路径：用户数据目录
        if not self.database_url:
            _ensure_user_data_dir()
            self.database_url = f"sqlite+aiosqlite:///{USER_DATA_DIR}/career_os.db"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


# ── config.json 双层配置 ──────────────────────────────


def load_user_config() -> dict[str, Any]:
    """读取用户运行时配置（~/.careeros/config.json）

    由 lifespan 或 config API 调用，叠加在 env/default 之上。
    Returns 空 dict 表示文件不存在或解析失败。
    """
    config_path = USER_DATA_DIR / "config.json"
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_user_config(data: dict[str, Any]) -> dict[str, Any]:
    """写入用户运行时配置，返回合并后的配置"""
    _ensure_user_data_dir()
    config_path = USER_DATA_DIR / "config.json"
    existing = load_user_config()
    existing.update(data)
    config_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    return existing


def apply_user_config(settings: Settings, user_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """将 config.json 中的值覆盖到 Settings 实例

    Returns 实际应用的配置项（供调试/日志使用）
    """
    cfg = load_user_config() if user_config is None else user_config
    applied: dict[str, Any] = {}

    # 新字段
    _CONFIG_KEYS = (
        "llm_provider",
        "llm_model",
        "llm_api_key",
        "llm_base_url",
        "embedding_provider",
        "embedding_model",
        "embedding_api_key",
        "embedding_base_url",
        "dashscope_api_key",  # 保留旧字段
    )

    for key in _CONFIG_KEYS:
        if cfg.get(key) is not None and getattr(settings, key, None) != cfg[key]:
            setattr(settings, key, cfg[key])
            # key 字段脱敏
            if "key" in key.lower():
                applied[key] = "***"
            else:
                applied[key] = cfg[key]

    # 一次性迁移：旧用户 dashscope_api_key → llm_api_key
    # 条件：llm_provider 是 dashscope（或未设置）且 llm_api_key 为空且 dashscope_api_key 非空
    if settings.llm_provider in ("dashscope", "") and not settings.llm_api_key and settings.dashscope_api_key:
        settings.llm_api_key = settings.dashscope_api_key
        # 同步写入 config.json，避免下次重启重复迁移
        save_user_config({"llm_api_key": settings.dashscope_api_key})
        applied["llm_api_key"] = "***（从 dashscope_api_key 迁移）"

    return applied
