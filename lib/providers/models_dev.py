"""models.dev 注册表集成 — 动态模型数据源。

从 https://models.dev/api.json 拉取，覆盖 4000+ 模型、109+ Provider。
提供 Provider 元数据、模型能力（context window、价格、tools/vision/reasoning）。

缓存策略（同 hermes-agent）：
  1. 内存缓存（1 小时 TTL）
  2. 磁盘缓存（~/.lumen/models_dev_cache.json，按 mtime 判断新鲜度）
  3. 网络拉取（https://models.dev/api.json）
  4. 失败时使用磁盘旧缓存（5 分钟宽限期后重试网络）
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

MODELS_DEV_URL = "https://models.dev/api.json"
_CACHE_TTL = 3600  # 1 小时

_mem_cache: dict[str, Any] = {}
_mem_cache_time: float = 0


# ── Lumen provider ID → models.dev provider ID ────────────────────────────────

PROVIDER_MAP: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "google",
    "gemini": "google",
    "dashscope": "alibaba",   # 阿里云 DashScope = alibaba
    "deepseek": "deepseek",
    "groq": "groq",
    "mistral": "mistral",
    "ollama": "ollama",
    "xai": "xai",
    "nvidia": "nvidia",
    "perplexity": "perplexity",
    "cohere": "cohere",
    "togetherai": "togetherai",
    "fireworks": "fireworks-ai",
    "huggingface": "huggingface",
}


# ── 数据类 ─────────────────────────────────────────────────────────────────────

@dataclass
class ModelInfo:
    id: str
    name: str
    provider_id: str
    family: str = ""
    reasoning: bool = False
    tool_call: bool = False
    attachment: bool = False
    structured_output: bool = False
    open_weights: bool = False
    input_modalities: tuple[str, ...] = ()
    output_modalities: tuple[str, ...] = ()
    context_window: int = 0
    max_output: int = 0
    cost_input: float = 0.0
    cost_output: float = 0.0
    cost_cache_read: Optional[float] = None
    knowledge_cutoff: str = ""
    status: str = ""

    def supports_vision(self) -> bool:
        return self.attachment or "image" in self.input_modalities

    def supports_pdf(self) -> bool:
        return "pdf" in self.input_modalities


@dataclass
class ProviderInfo:
    id: str
    name: str
    env: tuple[str, ...]
    api: str
    doc: str = ""
    model_count: int = 0


# ── 磁盘缓存 ───────────────────────────────────────────────────────────────────

def _cache_path() -> Path:
    p = Path.home() / ".lumen" / "models_dev_cache.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_disk() -> dict[str, Any]:
    try:
        p = _cache_path()
        if p.exists():
            return json.loads(p.read_bytes())
    except Exception as e:
        logger.debug("models.dev 磁盘缓存读取失败: %s", e)
    return {}


def _disk_age() -> Optional[float]:
    try:
        p = _cache_path()
        if not p.exists():
            return None
        age = time.time() - p.stat().st_mtime
        return age if age >= 0 else None
    except Exception:
        return None


def _save_disk(data: dict[str, Any]) -> None:
    try:
        p = _cache_path()
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        logger.debug("models.dev 磁盘缓存写入失败: %s", e)


# ── 主拉取函数 ─────────────────────────────────────────────────────────────────

def fetch_models_dev(force_refresh: bool = False) -> dict[str, Any]:
    """拉取 models.dev 注册表，四级缓存策略。"""
    global _mem_cache, _mem_cache_time

    # 1. 内存缓存
    if not force_refresh and _mem_cache and (time.time() - _mem_cache_time) < _CACHE_TTL:
        return _mem_cache

    # 2. 磁盘缓存（新鲜时跳过网络）
    if not force_refresh:
        age = _disk_age()
        if age is not None and age < _CACHE_TTL:
            data = _load_disk()
            if data:
                _mem_cache = data
                _mem_cache_time = time.time() - age
                logger.debug("models.dev 从磁盘缓存加载（%d providers，age=%.0fs）", len(data), age)
                return _mem_cache

    # 3. 网络拉取
    try:
        resp = httpx.get(MODELS_DEV_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data:
            _mem_cache = data
            _mem_cache_time = time.time()
            _save_disk(data)
            logger.debug(
                "models.dev 网络拉取成功：%d providers，%d 模型",
                len(data),
                sum(len(p.get("models", {})) for p in data.values() if isinstance(p, dict)),
            )
            return _mem_cache
    except Exception as e:
        logger.debug("models.dev 网络拉取失败: %s", e)

    # 4. 兜底：任意旧磁盘缓存（5 分钟宽限后重试网络）
    if not _mem_cache:
        _mem_cache = _load_disk()
        if _mem_cache:
            _mem_cache_time = time.time() - _CACHE_TTL + 300
            logger.debug("models.dev 使用旧磁盘缓存（%d providers）", len(_mem_cache))

    return _mem_cache


# ── 内部工具 ───────────────────────────────────────────────────────────────────

# 过滤掉 TTS、Embedding、纯图像、过时 preview 快照等噪音模型
_NOISE_RE = re.compile(
    r"-tts\b|embedding|live-|-(preview|exp)-\d{2,4}[-_]|-image\b|-image-preview\b",
    re.IGNORECASE,
)

_GOOGLE_HIDDEN: frozenset[str] = frozenset({
    "gemini-1.5-flash", "gemini-1.5-pro", "gemini-1.5-flash-8b",
    "gemini-2.0-flash", "gemini-2.0-flash-lite",
    "gemma-4-31b-it", "gemma-4-26b-it", "gemma-3-27b-it",
    "gemma-3-12b-it", "gemma-3-4b-it", "gemma-3-2b-it", "gemma-3-1b-it",
})


def _should_hide(provider: str, model_id: str) -> bool:
    if provider.lower() in {"gemini", "google"} and model_id.lower() in _GOOGLE_HIDDEN:
        return True
    return False


def _mdev_id(provider: str) -> Optional[str]:
    return PROVIDER_MAP.get(provider)


def _provider_models(provider: str) -> Optional[dict[str, Any]]:
    mdev = _mdev_id(provider)
    if not mdev:
        return None
    data = fetch_models_dev()
    pdata = data.get(mdev)
    if not isinstance(pdata, dict):
        return None
    models = pdata.get("models", {})
    return models if isinstance(models, dict) else None


def _find_entry(models: dict[str, Any], model_id: str) -> Optional[dict[str, Any]]:
    entry = models.get(model_id)
    if isinstance(entry, dict):
        return entry
    lower = model_id.lower()
    for mid, val in models.items():
        if mid.lower() == lower and isinstance(val, dict):
            return val
    return None


def _parse_model(model_id: str, raw: dict[str, Any], provider_id: str) -> ModelInfo:
    limit = raw.get("limit") or {}
    cost = raw.get("cost") or {}
    mods = raw.get("modalities") or {}
    inp_mods = mods.get("input") if isinstance(mods, dict) else None
    out_mods = mods.get("output") if isinstance(mods, dict) else None

    def _int(v: Any) -> int:
        return int(v) if isinstance(v, (int, float)) and v > 0 else 0

    def _float(v: Any) -> float:
        return float(v) if isinstance(v, (int, float)) else 0.0

    return ModelInfo(
        id=model_id,
        name=raw.get("name") or model_id,
        provider_id=provider_id,
        family=raw.get("family") or "",
        reasoning=bool(raw.get("reasoning", False)),
        tool_call=bool(raw.get("tool_call", False)),
        attachment=bool(raw.get("attachment", False)),
        structured_output=bool(raw.get("structured_output", False)),
        open_weights=bool(raw.get("open_weights", False)),
        input_modalities=tuple(inp_mods) if isinstance(inp_mods, list) else (),
        output_modalities=tuple(out_mods) if isinstance(out_mods, list) else (),
        context_window=_int(limit.get("context")),
        max_output=_int(limit.get("output")),
        cost_input=_float(cost.get("input")),
        cost_output=_float(cost.get("output")),
        cost_cache_read=float(cost["cache_read"]) if cost.get("cache_read") is not None else None,
        knowledge_cutoff=raw.get("knowledge") or "",
        status=raw.get("status") or "",
    )


# ── 公开查询接口 ───────────────────────────────────────────────────────────────

def list_provider_models(provider: str) -> list[str]:
    """返回 provider 下所有可用模型 ID。"""
    models = _provider_models(provider)
    if not models:
        return []
    return [mid for mid in models if not _should_hide(provider, mid)]


def list_agentic_models(provider: str) -> list[str]:
    """返回支持 tool_call 的模型 ID，过滤噪音模型。"""
    models = _provider_models(provider)
    if not models:
        return []
    result = []
    for mid, entry in models.items():
        if not isinstance(entry, dict):
            continue
        if _should_hide(provider, mid):
            continue
        if not entry.get("tool_call", False):
            continue
        if _NOISE_RE.search(mid):
            continue
        result.append(mid)
    return result


def get_model_info(provider: str, model_id: str) -> Optional[ModelInfo]:
    """查询单个模型的完整元数据。"""
    mdev = _mdev_id(provider) or provider
    data = fetch_models_dev()
    pdata = data.get(mdev)
    if not isinstance(pdata, dict):
        return None
    models = pdata.get("models", {})
    if not isinstance(models, dict):
        return None
    raw = _find_entry(models, model_id)
    if raw is None:
        return None
    return _parse_model(model_id, raw, mdev)


def get_provider_info(provider: str) -> Optional[ProviderInfo]:
    """查询 provider 元数据（name、api base_url、env vars）。"""
    mdev = _mdev_id(provider) or provider
    data = fetch_models_dev()
    raw = data.get(mdev)
    if not isinstance(raw, dict):
        return None
    env = raw.get("env") or []
    models = raw.get("models") or {}
    return ProviderInfo(
        id=mdev,
        name=raw.get("name") or mdev,
        env=tuple(env) if isinstance(env, list) else (),
        api=raw.get("api") or "",
        doc=raw.get("doc") or "",
        model_count=len(models) if isinstance(models, dict) else 0,
    )


def lookup_context_window(provider: str, model_id: str) -> Optional[int]:
    """查询模型的 context window，找不到返回 None。"""
    info = get_model_info(provider, model_id)
    return info.context_window if info and info.context_window > 0 else None
