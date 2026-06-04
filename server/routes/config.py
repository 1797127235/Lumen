"""用户配置 API"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from core.config import (
    apply_user_config,
    get_provider_catalog_frontend,
    get_settings,
    load_user_config,
    save_user_config,
)

router = APIRouter(tags=["config"])


class ConfigResponse(BaseModel):
    llm_provider: str = "dashscope"
    llm_model: str = "qwen-plus"
    llm_api_key: str = ""
    llm_base_url: str = ""
    has_llm_key: bool = False
    context_window: int = 128_000
    embedding_provider: str = "dashscope"
    embedding_model: str = "text-embedding-v4"
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    has_embedding_key: bool = False
    dashscope_api_key: str = ""
    has_api_key: bool = False
    # VL 模型配置
    vl_provider: str = ""
    vl_model: str = ""
    has_vl_key: bool = False


class ConfigUpdate(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_api_key: str | None = None
    embedding_base_url: str | None = None
    dashscope_api_key: str | None = None
    providers: dict[str, Any] | None = None
    # VL 模型配置
    vl_provider: str | None = None
    vl_model: str | None = None
    vl_api_key: str | None = None
    vl_base_url: str | None = None


class ConfigTestRequest(BaseModel):
    provider: str
    model: str
    api_key: str
    base_url: str = ""


class ConfigTestResponse(BaseModel):
    ok: bool
    latency_ms: int = 0
    error: str = ""


_CONTEXT_WINDOW_PATTERNS: list[tuple[str, int]] = [
    # DeepSeek V4+ — 1M context
    ("deepseek-v4", 1_000_000),
    # Gemini long-context variants
    ("gemini-1.5-pro", 2_097_152),
    ("gemini-1.5-flash", 1_048_576),
    ("gemini-2", 1_048_576),
    # Claude 3.x / 4.x — 200k
    ("claude-3", 200_000),
    ("claude-4", 200_000),
    # GPT-4 128k
    ("gpt-4-turbo", 128_000),
    ("gpt-4o", 128_000),
    # Qwen long-context
    ("qwen-long", 1_000_000),
    ("qwen2.5-72b", 131_072),
]


def _resolve_context_window(provider: str, model: str, user_override: int | None) -> int:
    if user_override and user_override > 0:
        return user_override

    # Try litellm first
    try:
        import litellm

        candidates = [f"{provider}/{model}", model]
        for name in candidates:
            try:
                info = litellm.get_model_info(name)
                ctx = info.get("max_input_tokens") or info.get("max_tokens")
                if isinstance(ctx, int) and ctx > 0:
                    return ctx
            except Exception:
                pass
    except ImportError:
        pass

    # Pattern-based fallback (longest-match-first via ordering)
    model_lower = model.lower()
    for pattern, ctx in _CONTEXT_WINDOW_PATTERNS:
        if pattern in model_lower:
            return ctx

    return 128_000


@router.get("/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    user_config = load_user_config()
    settings = get_settings()

    has_llm_key = bool(user_config.get("llm_api_key") or settings.llm_api_key)
    has_embedding_key = bool(
        user_config.get("embedding_api_key")
        or settings.embedding_api_key
        or user_config.get("llm_api_key")
        or settings.llm_api_key
    )
    # has_api_key 保留向后兼容，但实际只检查 llm_api_key
    has_api_key = has_llm_key

    def mask_key(value: str) -> str:
        return "***" if value else ""

    provider = user_config.get("llm_provider") or settings.llm_provider
    model = user_config.get("llm_model") or settings.llm_model
    user_ctx = user_config.get("llm_context_limit")
    context_window = _resolve_context_window(provider, model, int(user_ctx) if user_ctx else None)

    return ConfigResponse(
        llm_provider=provider,
        llm_model=model,
        llm_api_key=mask_key(user_config.get("llm_api_key") or settings.llm_api_key),
        llm_base_url=user_config.get("llm_base_url") or settings.llm_base_url,
        has_llm_key=has_llm_key,
        context_window=context_window,
        embedding_provider=user_config.get("embedding_provider") or settings.embedding_provider,
        embedding_model=user_config.get("embedding_model") or settings.embedding_model,
        embedding_api_key=mask_key(user_config.get("embedding_api_key") or settings.embedding_api_key),
        embedding_base_url=user_config.get("embedding_base_url") or settings.embedding_base_url,
        has_embedding_key=has_embedding_key,
        dashscope_api_key="",  # 已废弃，保留字段向后兼容
        has_api_key=has_api_key,
        # VL 模型配置
        vl_provider=user_config.get("vl_provider") or "",
        vl_model=user_config.get("vl_model") or "",
        has_vl_key=bool(user_config.get("vl_api_key") or user_config.get("llm_api_key") or settings.llm_api_key),
    )


@router.post("/config", response_model=ConfigResponse)
async def update_config(body: ConfigUpdate) -> ConfigResponse:
    data: dict[str, str] = {}
    for field in body.model_fields:
        value = getattr(body, field)
        if value is not None:
            data[field] = value

    if data:
        merged = save_user_config(data)
        apply_user_config(get_settings(), merged)

    return await get_config()


@router.get("/config/providers")
async def get_providers():
    """返回 Provider 目录（供前端动态拉取，避免前后端重复维护）"""
    return get_provider_catalog_frontend()


@router.post("/config/test", response_model=ConfigTestResponse)
async def test_config(body: ConfigTestRequest) -> ConfigTestResponse:
    import time

    import litellm

    start = time.time()
    api_key = body.api_key
    if not api_key:
        user_config = load_user_config()
        settings = get_settings()
        api_key = user_config.get("llm_api_key") or settings.llm_api_key

    try:
        from core.config import build_llm_call_params

        llm_params = build_llm_call_params(
            model=body.model,
            provider=body.provider,
            api_key=api_key,
            base_url=body.base_url,
        )
        kwargs: dict = {
            "model": llm_params["model"],
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.7,
            "max_tokens": 10,
            "api_key": llm_params["api_key"],
            "stream": False,
            "timeout": 30,
        }
        if llm_params["base_url"]:
            kwargs["base_url"] = llm_params["base_url"]

        await litellm.acompletion(**kwargs)
        return ConfigTestResponse(ok=True, latency_ms=int((time.time() - start) * 1000))
    except Exception as exc:
        error_type = type(exc).__name__
        error_msg = str(exc)[:100] if len(str(exc)) <= 100 else f"{str(exc)[:100]}..."
        return ConfigTestResponse(ok=False, error=f"{error_type}: {error_msg}")
