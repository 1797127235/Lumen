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

    # ── 核心鉴权 ──
    dashscope_api_key: str = ""

    # ── 模型配置 ──
    embedding_model: str = "text-embedding-v4"

    # ── 数据库 ──
    database_url: str = ""

    # ── 应用 ──
    debug: bool = True
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

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

    for key in ("dashscope_api_key",):
        if cfg.get(key) and getattr(settings, key, None) != cfg[key]:
            setattr(settings, key, cfg[key])
            applied[key] = "***"

    return applied
