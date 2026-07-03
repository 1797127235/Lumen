"""应用配置 — 环境变量 + config.json 双层配置

优先级: config.json > 环境变量 (.env) > 默认值
USER_DATA_DIR: ~/.lumen/（用户运行时数据，跨版本持久化）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.logging import get_logger

logger = get_logger(__name__)

# ── 目录常量 ────────────────────────────────────────

# 用户运行时数据目录（SQLite / Chroma / config.json）
USER_DATA_DIR = Path.home() / ".lumen"


def _ensure_user_data_dir() -> None:
    """确保用户数据目录存在"""
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── Settings ─────────────────────────────────────────


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parents[1] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM Provider ──
    llm_provider: str = "dashscope"
    llm_model: str = "qwen-plus"
    llm_api_key: str = ""
    llm_base_url: str = ""
    # 模型上下文窗口上限（token）。0 = 由 CLI 从 models.dev 解析（带本地缓存）。
    llm_context_limit: int = 0

    # ── Embedding Provider ──
    embedding_provider: str = "dashscope"
    embedding_model: str = "text-embedding-v4"
    embedding_api_key: str = ""  # 空 = 使用 llm_api_key
    embedding_base_url: str = ""

    # ── 数据库 ──
    database_url: str = ""

    # ── Agent 工作目录 ──
    # Agent 文件工具可访问的根目录（用于开发时访问项目文件）
    # 默认使用用户主目录（最安全），可配置为项目根目录以方便开发
    # 示例：AGENT_WORKSPACE_DIR=E:\\MyHub\\career-os
    agent_workspace_dir: str = ""

    # ── 网络搜索 ──
    search_provider: str = ""  # tavily / serper / brave
    search_api_key: str = ""

    # ── 外部数据接入 ──
    external_data_enabled: bool = False
    external_data_dirs: str = ""
    # 格式：逗号分隔的目录路径，如 "C:\\Obsidian,C:\\Notes"

    @property
    def external_data_dir_list(self) -> list[str]:
        """解析逗号分隔的目录路径为列表。"""
        if not self.external_data_dirs:
            return []
        return [d.strip() for d in self.external_data_dirs.split(",") if d.strip()]

    # ── 应用 ──
    debug: bool = True
    # ── 多平台通道 ──
    enable_web: bool = True
    telegram_bot_token: str = ""
    # ── Telegram push target ──
    telegram_chat_id: str = ""  # 首次 Telegram 对话时自动填充

    # ── 语义去重 ──
    semantic_dedup_enabled: bool = False
    semantic_dedup_default_threshold: float = 0.85
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:5174", "http://localhost:3000"]

    # ── 后台记忆审查 ──
    # 每隔多少条消息触发一次后台审查。1 = 每条消息都审（默认，保持旧行为）；10 = 每 10 条消息审一次
    memory_review_interval: int = 1

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # 数据库默认路径：用户数据目录
        if not self.database_url:
            _ensure_user_data_dir()
            self.database_url = f"sqlite+aiosqlite:///{USER_DATA_DIR}/lumen.db"
        # 修复 Windows .env 中文编码问题：直接读取 .env 文件覆盖
        env_path = Path(__file__).parents[1] / ".env"
        if env_path.exists():
            raw = env_path.read_text(encoding="utf-8")
            for line in raw.splitlines():
                if line.startswith("EXTERNAL_DATA_DIRS="):
                    val = line[len("EXTERNAL_DATA_DIRS=") :].strip()
                    if val:
                        self.external_data_dirs = val
                    break


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


# ── config.json 双层配置 ──────────────────────────────


def load_user_config() -> dict[str, Any]:
    """读取用户运行时配置（~/.lumen/config.json）

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

    # 顶层标量字段浅合并（过滤空值，不动 providers）
    flat = {
        k: v
        for k, v in data.items()
        if k != "providers" and v not in (None, "") and not (isinstance(v, str) and not v.strip())
    }
    existing.update(flat)

    # providers 深合并
    # value 为 None → 删除该供应商（照抄 remove()）
    # value 为 dict → 字段级合并（照抄 saveProvider() 的 partial update）
    if "providers" in data and isinstance(data["providers"], dict):
        ep = existing.get("providers") or {}
        for pid, pval in data["providers"].items():
            if pval is None:
                ep.pop(pid, None)
            elif isinstance(pval, dict):
                ep[pid] = {**(ep.get(pid) or {}), **{k: v for k, v in pval.items() if v is not None}}
        existing["providers"] = ep

    tmp = config_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(config_path)  # 原子写入
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
        "llm_context_limit",
        "embedding_provider",
        "embedding_model",
        "embedding_api_key",
        "embedding_base_url",
        "telegram_chat_id",
        "memory_review_interval",
    )

    for key in _CONFIG_KEYS:
        val = cfg.get(key)
        if val is None or val == "":
            continue

        # 类型校验/转换
        if key == "memory_review_interval":
            try:
                val = int(val)
            except (TypeError, ValueError):
                logger.warning("memory_review_interval 配置无效，忽略", value=val)
                continue
            if val < 1:
                logger.warning("memory_review_interval 必须 >= 1，忽略", value=val)
                continue

        if getattr(settings, key, None) != val:
            setattr(settings, key, val)
            # key 字段脱敏
            if "key" in key.lower():
                applied[key] = "***"
            else:
                applied[key] = val

    # 一次性迁移：旧用户 dashscope_api_key → llm_api_key
    # 条件：llm_provider 是 dashscope（或未设置）且 llm_api_key 为空且 dashscope_api_key 非空
    if settings.llm_provider in ("dashscope", "") and not settings.llm_api_key and settings.dashscope_api_key:
        settings.llm_api_key = settings.dashscope_api_key
        # 同步写入 config.json，避免下次重启重复迁移
        save_user_config({"llm_api_key": settings.dashscope_api_key})
        applied["llm_api_key"] = "***（从 dashscope_api_key 迁移）"

    return applied


def build_llm_call_params(
    model: str | None = None,
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, str]:
    """构建 LLM 调用参数（model_id, api_key, base_url）

    统一 model_id 构建规则（provider != openai 时加前缀）和认证参数获取逻辑，
    避免 summary.py、test_config 等处重复实现。
    优先从 providers 配置中读取 key 和 base_url（供应商页面管理），
    其次回退到顶层字段（兼容旧配置）。
    """
    settings = get_settings()
    provider = provider or settings.llm_provider
    model = model or settings.llm_model
    model_id = model if provider == "openai" else f"{provider}/{model}"

    # 优先从供应商页面配置读取
    user_cfg = load_user_config()
    provider_cfg = (user_cfg.get("providers") or {}).get(provider, {})
    provider_key = provider_cfg.get("api_key", "")
    provider_base_url = provider_cfg.get("base_url", "")

    return {
        "model": model_id,
        "api_key": api_key or settings.llm_api_key or provider_key or settings.dashscope_api_key or "",
        "base_url": base_url or settings.llm_base_url or provider_base_url,
    }


def get_provider_catalog_frontend() -> dict[str, dict]:
    """返回前端所需的 Provider 配置格式，优先使用 models.dev 动态数据。"""
    from lib.providers import get_provider_registry

    summary = get_provider_registry().get_all_summary()
    return {
        p["id"]: {
            "name": p["name"],
            "baseUrl": p["baseUrl"],
            "models": p["models"],
            "embeddingModels": p["embeddingModels"],
        }
        for p in summary
    }
