"""用户配置路由 — 读写 ~/.careeros/config.json"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.backend.config import apply_user_config, get_settings, load_user_config, save_user_config

router = APIRouter(tags=["config"])


# ── Provider 内置配置表 ──────────────────────────────

_PROVIDER_DEFAULTS: dict[str, dict] = {
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "chat_models": ["qwen-plus", "qwen-max", "qwen-turbo"],
        "embedding_models": ["text-embedding-v4"],
    },
    "openai": {
        "base_url": "",
        "chat_models": ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
        "embedding_models": ["text-embedding-3-small", "text-embedding-3-large"],
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "chat_models": ["deepseek-chat", "deepseek-reasoner"],
        "embedding_models": [],
    },
    "anthropic": {
        "base_url": "",
        "chat_models": ["claude-3-5-sonnet-20241022", "claude-3-opus-20240229"],
        "embedding_models": [],
    },
    "gemini": {
        "base_url": "",
        "chat_models": ["gemini-1.5-pro", "gemini-1.5-flash"],
        "embedding_models": ["models/text-embedding-004"],
    },
    "ollama": {
        "base_url": "http://localhost:11434",
        "chat_models": ["llama3.1", "qwen2.5", "mistral"],
        "embedding_models": ["nomic-embed-text", "mxbai-embed-large"],
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "chat_models": ["openai/gpt-4o", "anthropic/claude-3.5-sonnet", "meta-llama/llama-3.1-70b"],
        "embedding_models": [],
    },
    "custom": {
        "base_url": "",
        "chat_models": [],
        "embedding_models": [],
    },
}


# ── Response/Update 模型 ──────────────────────────────


class ConfigResponse(BaseModel):
    # LLM
    llm_provider: str = "dashscope"
    llm_model: str = "qwen-plus"
    llm_api_key: str = ""  # 返回时脱敏
    llm_base_url: str = ""
    has_llm_key: bool = False

    # Embedding
    embedding_provider: str = "dashscope"
    embedding_model: str = "text-embedding-v4"
    embedding_api_key: str = ""  # 返回时脱敏
    embedding_base_url: str = ""
    has_embedding_key: bool = False

    # 旧字段（向后兼容）
    dashscope_api_key: str = ""
    has_api_key: bool = False


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


class ConfigTestRequest(BaseModel):
    provider: str
    model: str
    api_key: str
    base_url: str = ""


class ConfigTestResponse(BaseModel):
    ok: bool
    latency_ms: int = 0
    error: str = ""


# ── 路由 ──────────────────────────────────────────────


@router.get("/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    """获取当前用户配置"""
    user_config = load_user_config()
    settings = get_settings()

    # API Key 判断 — fallback 到旧 dashscope_api_key
    has_llm_key = bool(
        user_config.get("llm_api_key")
        or settings.llm_api_key
        or user_config.get("dashscope_api_key")
        or settings.dashscope_api_key
    )
    has_embedding_key = bool(
        user_config.get("embedding_api_key")
        or settings.embedding_api_key
        or user_config.get("llm_api_key")
        or settings.llm_api_key
        or user_config.get("dashscope_api_key")
        or settings.dashscope_api_key
    )
    has_api_key = bool(user_config.get("dashscope_api_key") or settings.dashscope_api_key)

    def mask_key(val: str) -> str:
        return "***" if val else ""

    return ConfigResponse(
        llm_provider=user_config.get("llm_provider") or settings.llm_provider,
        llm_model=user_config.get("llm_model") or settings.llm_model,
        llm_api_key=mask_key(user_config.get("llm_api_key") or settings.llm_api_key),
        llm_base_url=user_config.get("llm_base_url") or settings.llm_base_url,
        has_llm_key=has_llm_key,
        embedding_provider=user_config.get("embedding_provider") or settings.embedding_provider,
        embedding_model=user_config.get("embedding_model") or settings.embedding_model,
        embedding_api_key=mask_key(user_config.get("embedding_api_key") or settings.embedding_api_key),
        embedding_base_url=user_config.get("embedding_base_url") or settings.embedding_base_url,
        has_embedding_key=has_embedding_key,
        dashscope_api_key=mask_key(user_config.get("dashscope_api_key") or settings.dashscope_api_key),
        has_api_key=has_api_key,
    )


@router.post("/config", response_model=ConfigResponse)
async def update_config(body: ConfigUpdate) -> ConfigResponse:
    """更新用户配置"""
    data = {}
    for field in body.model_fields:
        val = getattr(body, field)
        if val is not None:
            data[field] = val

    if data:
        merged = save_user_config(data)
        apply_user_config(get_settings(), merged)

    return await get_config()


@router.post("/config/test", response_model=ConfigTestResponse)
async def test_config(body: ConfigTestRequest) -> ConfigTestResponse:
    """测试 LLM 连接"""
    import time

    from app.backend.agent.llm_router import chat

    start = time.time()
    try:
        await chat(
            "general_chat",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.7,
            max_tokens=10,
            api_key=body.api_key,
            base_url=body.base_url or None,
            model=f"{body.provider}/{body.model}" if body.provider != "openai" else body.model,
        )
        return ConfigTestResponse(ok=True, latency_ms=int((time.time() - start) * 1000))
    except Exception as e:
        return ConfigTestResponse(ok=False, error=str(e)[:200])
