"""Provider 目录 — 最小化实现，满足 core/config.py 和 server/routes/providers.py 的 import 需求

后续可在此扩展完整的 Provider 注册表、动态发现、凭证管理等功能。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 避免循环导入：不依赖 core.config，直接内联定义
_USER_DATA_DIR = Path.home() / ".lumen"

# ── Provider 目录 ───────────────────────────────────────────
# 与 core/config.py 的 get_provider_catalog_frontend() 对应

PROVIDER_CATALOG: dict[str, dict[str, Any]] = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "chat_models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
        "embedding_models": ["text-embedding-3-small", "text-embedding-3-large"],
    },
    "dashscope": {
        "label": "阿里云 DashScope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "chat_models": ["qwen-max", "qwen-plus", "qwen-turbo", "qwen2.5-72b-instruct"],
        "embedding_models": ["text-embedding-v4", "text-embedding-v3"],
    },
    "anthropic": {
        "label": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "chat_models": ["claude-3-5-sonnet-20241022", "claude-3-opus-20240229", "claude-3-haiku-20240307"],
        "embedding_models": [],
    },
    "google": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "chat_models": ["gemini-1.5-pro", "gemini-1.5-flash"],
        "embedding_models": ["text-embedding-004"],
    },
    "ollama": {
        "label": "Ollama (本地)",
        "base_url": "http://localhost:11434",
        "chat_models": ["llama3", "qwen2.5", "mistral"],
        "embedding_models": ["nomic-embed-text", "mxbai-embed-large"],
    },
    "siliconflow": {
        "label": "SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "chat_models": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct"],
        "embedding_models": ["BAAI/bge-large-zh-v1.5"],
    },
}


# ── ProviderRegistry ────────────────────────────────────────


@dataclass
class ProviderRegistry:
    """Provider 注册表 — 管理 provider 元数据、凭证、模型缓存"""

    _catalog: dict[str, dict[str, Any]] = field(default_factory=lambda: PROVIDER_CATALOG.copy())
    _custom_models: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)

    def get_all_summary(self) -> list[dict[str, Any]]:
        """返回所有 provider 的摘要列表（供前端选择器使用）"""
        result = []
        for key, val in self._catalog.items():
            result.append(
                {
                    "id": key,
                    "name": val["label"],
                    "baseUrl": val.get("base_url", ""),
                    "models": val.get("chat_models", []),
                    "embeddingModels": val.get("embedding_models", []),
                }
            )
        return result

    def get_default_models(self, name: str) -> list[str]:
        """返回内置默认模型列表"""
        return self._catalog.get(name, {}).get("chat_models", [])

    def get_credentials(self, name: str | None) -> dict[str, Any] | None:
        """读取用户保存的 provider 凭证（从 config.json）"""
        if not name:
            return None
        try:
            config_path = _USER_DATA_DIR / "config.json"
            if not config_path.exists():
                return None
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            providers = cfg.get("providers", {})
            return providers.get(name)
        except Exception:
            return None

    def update_model_entry(self, name: str, model_id: str, meta: dict[str, Any]) -> None:
        """更新自定义模型元数据"""
        if name not in self._custom_models:
            self._custom_models[name] = {}
        self._custom_models[name][model_id] = meta

    def get_model_meta(self, name: str, model_id: str) -> dict[str, Any] | None:
        """获取模型元数据（先查自定义，再查内置）"""
        custom = self._custom_models.get(name, {}).get(model_id)
        if custom:
            return custom
        # 内置目录中无元数据，返回空
        return None


# ── 模块级单例 ──────────────────────────────────────────────

_registry: ProviderRegistry | None = None


def get_provider_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry
