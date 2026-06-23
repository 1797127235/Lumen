"""models.dev 定价数据拉取与缓存。

数据源: https://models.dev/api.json
缓存: ~/.lumen/cache/models_dev.json（24h 过期）

只负责拉取、缓存、查询定价，不耦合 metrics 或其他模块。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from shared.logging import get_logger

logger = get_logger(__name__)

MODELS_DEV_URL = "https://models.dev/api.json"
CACHE_FILE = Path.home() / ".lumen" / "cache" / "models_dev.json"
CACHE_TTL = 86400  # 24h

# 内存缓存
_cache: dict[str, Any] | None = None
_cache_time: float = 0.0


@dataclass
class ModelPricing:
    """单个模型的定价信息（USD / 百万 token）。"""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


def _load_disk_cache() -> dict[str, Any] | None:
    """从磁盘缓存加载。"""
    try:
        if CACHE_FILE.exists():
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "providers" in data:
                return data
    except Exception:
        logger.debug("磁盘缓存加载失败")
    return None


def _save_disk_cache(data: dict[str, Any]) -> None:
    """保存到磁盘缓存。"""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        logger.debug("磁盘缓存保存失败")


async def _fetch_from_network() -> dict[str, Any] | None:
    """从 models.dev 拉取。"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(MODELS_DEV_URL)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning("models.dev 拉取失败: %s", e)
    return None


async def _ensure_loaded() -> dict[str, Any]:
    """确保数据已加载（内存 → 磁盘 → 网络）。"""
    global _cache, _cache_time

    now = time.time()
    if _cache and (now - _cache_time) < CACHE_TTL:
        return _cache

    disk = _load_disk_cache()
    if disk:
        _cache = disk
        _cache_time = now
        logger.debug("从磁盘缓存加载 models.dev")
        return disk

    network = await _fetch_from_network()
    if network:
        _cache = network
        _cache_time = now
        _save_disk_cache(network)
        logger.info("从 models.dev 拉取定价数据")
        return network

    if _cache:
        return _cache

    return {"providers": {}}


def _find_model_pricing(data: dict[str, Any], model_name: str) -> ModelPricing | None:
    """在 providers 中查找模型定价。

    遍历所有 provider 的 models，匹配 model id。
    """
    providers = data.get("providers", {})
    for provider in providers.values():
        models = provider.get("models", {})
        if model_name in models:
            cost = models[model_name].get("cost", {})
            if cost:
                return ModelPricing(
                    input=float(cost.get("input", 0)),
                    output=float(cost.get("output", 0)),
                    cache_read=float(cost.get("cache_read", 0)),
                    cache_write=float(cost.get("cache_write", 0)),
                )
    return None


async def get_model_pricing(model_name: str) -> ModelPricing:
    """获取模型定价。未找到返回默认值 0。"""
    data = await _ensure_loaded()
    return _find_model_pricing(data, model_name) or ModelPricing()


async def get_all_pricing() -> dict[str, ModelPricing]:
    """获取所有模型定价（用于批量计算）。"""
    data = await _ensure_loaded()
    result: dict[str, ModelPricing] = {}
    providers = data.get("providers", {})
    for provider in providers.values():
        models = provider.get("models", {})
        for model_id, model_info in models.items():
            cost = model_info.get("cost", {})
            if cost:
                result[model_id] = ModelPricing(
                    input=float(cost.get("input", 0)),
                    output=float(cost.get("output", 0)),
                    cache_read=float(cost.get("cache_read", 0)),
                    cache_write=float(cost.get("cache_write", 0)),
                )
    return result


async def calculate_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """计算单次调用成本（USD）。"""
    pricing = await get_model_pricing(model_name)
    return (
        input_tokens * pricing.input
        + output_tokens * pricing.output
        + cache_read_tokens * pricing.cache_read
        + cache_write_tokens * pricing.cache_write
    ) / 1_000_000
