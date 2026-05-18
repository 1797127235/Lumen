"""供应商管理路由 — 照抄 openhanako server/routes/providers.js"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.config import USER_DATA_DIR
from lib.providers import get_provider_registry
from lib.providers._client import build_auth_headers, probe_provider
from lib.providers._validation import filter_discovered_models

router = APIRouter(tags=["providers"])

# ── models-cache 读写（照抄 readModelsCache / writeModelsCache）──────


def _cache_path() -> Path:
    return USER_DATA_DIR / "models-cache.json"


def _read_cache() -> dict:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cache(cache: dict) -> None:
    """照抄原子写入：tmp + rename"""
    target = _cache_path()
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)


def _save_to_cache(provider_name: str, models: list) -> None:
    """照抄 saveToCache()"""
    if not provider_name or not models:
        return
    from datetime import UTC, datetime

    try:
        cache = _read_cache()
        cache[provider_name] = {"models": models, "fetchedAt": datetime.now(UTC).isoformat()}
        _write_cache(cache)
    except Exception:
        pass


# ── 模型列表规范化（照抄 normalizeRemoteModels）──────────────────────


def _normalize_remote_models(data: dict, api: str) -> list:
    if api == "anthropic-messages":
        return [
            {
                "id": m["id"],
                "name": m.get("display_name", m["id"]),
                "context": m.get("max_input_tokens"),
                "maxOutput": m.get("max_tokens"),
            }
            for m in data.get("data", [])
        ]
    if api == "google-generative-ai":
        result = []
        for m in data.get("models", []):
            mid = m.get("baseModelId") or (m.get("name", "")).replace("models/", "")
            if mid:
                result.append(
                    {
                        "id": mid,
                        "name": m.get("displayName", mid),
                        "context": m.get("inputTokenLimit"),
                        "maxOutput": m.get("outputTokenLimit"),
                    }
                )
        return result
    # openai-completions 默认
    return [
        {
            "id": m["id"],
            "name": m["id"],
            "context": m.get("context_length") or m.get("context_window") or m.get("max_context_length"),
            "maxOutput": m.get("max_completion_tokens") or m.get("max_output_tokens"),
        }
        for m in data.get("data", [])
    ]


def _registry_or_defaults_fallback(name: str) -> dict:
    """照抄 registryOrDefaultsFallback()"""
    if not name:
        return {"error": "name is required for fallback", "models": []}
    defaults = get_provider_registry().get_default_models(name)
    if defaults:
        models = [{"id": mid, "name": mid, "context": None, "maxOutput": None} for mid in defaults]
        filtered, _ = filter_discovered_models(name, models)
        _save_to_cache(name, filtered)
        return {"source": "builtin", "models": filtered}
    return {"error": f'No models found for provider "{name}"', "models": []}


# ── Routes ───────────────────────────────────────────────────────────


@router.get("/providers/summary")
async def get_providers_summary():
    """照抄 GET /providers/summary"""
    summary = get_provider_registry().get_all_summary()
    return {"providers": summary}


class FetchModelsRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api: str | None = None
    api_key: str | None = None


@router.post("/providers/fetch-models")
async def fetch_models(body: FetchModelsRequest):
    """照抄 POST /providers/fetch-models"""
    if not body.name and not body.base_url:
        return JSONResponse({"error": "name or base_url is required"}, status_code=400)

    # 凭证优先级：body > saved（照抄）
    saved = get_provider_registry().get_credentials(body.name) if body.name else {}
    saved = saved or {}
    effective_key = body.api_key or saved.get("api_key", "")
    effective_url = (body.base_url or saved.get("base_url", "")).rstrip("/")
    effective_api = body.api or saved.get("api", "")

    if effective_url:
        try:
            if effective_api == "anthropic-messages":
                url = f"{effective_url}/v1/models?limit=1000"
            elif effective_api == "google-generative-ai":
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={effective_key}"
            else:
                url = f"{effective_url}/models"

            headers: dict = {}
            if effective_key and effective_api:
                headers = build_auth_headers(effective_api, effective_key, allow_missing=True)

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=headers)

            # 401/403：直接返回错误，不 fallback（照抄）
            if resp.status_code in (401, 403):
                return {"error": f"HTTP {resp.status_code}: {resp.reason_phrase}", "models": []}

            if resp.is_success:
                data = resp.json()
                remote = _normalize_remote_models(data, effective_api)
                filtered, ignored = filter_discovered_models(body.name or "", remote, effective_url)
                if not filtered and ignored:
                    return {
                        "error": f"Remote only returned invalid model ids: {ignored}",
                        "models": [],
                        "ignoredModels": ignored,
                    }
                _save_to_cache(body.name, filtered)
                result = {"models": filtered}
                if ignored:
                    result["ignoredModels"] = ignored
                return result
        except Exception:
            pass  # 网络错误 → fallback

    return _registry_or_defaults_fallback(body.name or "")


@router.get("/providers/{name}/discovered-models")
async def get_discovered_models(name: str):
    """照抄 GET /providers/:name/discovered-models"""
    cache = _read_cache()
    entry = cache.get(name)
    if not entry:
        return {"models": [], "fetchedAt": None}
    creds = get_provider_registry().get_credentials(name) or {}
    filtered, _ = filter_discovered_models(name, entry.get("models", []), creds.get("base_url", ""))
    return {"models": filtered, "fetchedAt": entry.get("fetchedAt")}


class TestRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api: str | None = None
    api_key: str | None = None


@router.post("/providers/test")
async def test_provider(body: TestRequest):
    """照抄 POST /providers/test"""
    # api_key 清洗（照抄：去掉非 ASCII 字符）
    raw_key = body.api_key or ""
    clean_key = re.sub(r"[^\x20-\x7E]", "", raw_key).strip()

    saved = get_provider_registry().get_credentials(body.name) if body.name else {}
    saved = saved or {}
    api_key = clean_key or saved.get("api_key", "")
    base_url = body.base_url or saved.get("base_url", "")
    api = body.api or saved.get("api", "")

    if not base_url:
        return JSONResponse({"error": "base_url is required"}, status_code=400)
    if api_key and not api:
        return JSONResponse({"error": "api is required when api_key is present"}, status_code=400)

    return await probe_provider(base_url, api, api_key)


class ModelMetaRequest(BaseModel):
    name: str | None = None
    context: int | None = None
    maxOutput: int | None = None
    image: bool | None = None
    video: bool | None = None
    reasoning: bool | None = None


@router.put("/providers/{name}/models/{model_id}")
async def update_model(name: str, model_id: str, body: ModelMetaRequest):
    """照抄 PUT /providers/:name/models/:modelId"""
    from urllib.parse import unquote

    model_id = unquote(model_id)
    try:
        meta = body.model_dump(exclude_none=True)
        get_provider_registry().update_model_entry(name, model_id, meta)
        return {"ok": True}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404 if "not found" in str(e).lower() else 400)
