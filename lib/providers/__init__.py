"""Provider 注册表 — 完全基于 models.dev 动态数据。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_USER_DATA_DIR = Path.home() / ".lumen"


@dataclass
class ProviderRegistry:
    _custom_models: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)

    def get_all_summary(self) -> list[dict[str, Any]]:
        from lib.providers.models_dev import PROVIDER_MAP, fetch_models_dev, list_provider_models

        mdev = fetch_models_dev()
        result = []
        for lumen_id, mdev_id in PROVIDER_MAP.items():
            pdata = mdev.get(mdev_id)
            if not isinstance(pdata, dict):
                continue
            result.append(
                {
                    "id": lumen_id,
                    "name": pdata.get("name") or lumen_id,
                    "baseUrl": pdata.get("api") or "",
                    "models": list_provider_models(lumen_id),
                    "embeddingModels": [],
                }
            )
        return result

    def get_default_models(self, name: str) -> list[str]:
        from lib.providers.models_dev import list_provider_models

        return list_provider_models(name)

    def get_credentials(self, name: str | None) -> dict[str, Any] | None:
        if not name:
            return None
        try:
            config_path = _USER_DATA_DIR / "config.json"
            if not config_path.exists():
                return None
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            return cfg.get("providers", {}).get(name)
        except Exception:
            return None

    def update_model_entry(self, name: str, model_id: str, meta: dict[str, Any]) -> None:
        if name not in self._custom_models:
            self._custom_models[name] = {}
        self._custom_models[name][model_id] = meta

    def get_model_meta(self, name: str, model_id: str) -> dict[str, Any] | None:
        return self._custom_models.get(name, {}).get(model_id)


_registry: ProviderRegistry | None = None


def get_provider_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry
