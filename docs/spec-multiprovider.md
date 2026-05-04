# CareerOS 多 LLM Provider 改造实施规格

## 目标

把目前硬编码 DashScope 的 LLM 调用层，改造为通过 LiteLLM SDK 支持多 Provider 切换。用户可在 Settings 页面选择 Provider 并配置对应参数，无需改代码或 .env 文件。

**支持的 Provider（一期）**：DashScope（默认）、OpenAI、DeepSeek、Anthropic、Gemini、Ollama（本地）、OpenRouter、自定义 OpenAI-Compatible。

## 现状

- `llm_router.py` 已引入 `litellm`（`pip install litellm>=1.30.0` 已在 requirements.txt），但模型名、api_key、base_url 全部硬编码为 DashScope。
- `rag.py` 的 embedding 通过 `llm_router.embed()` 调用，同样硬编码。
- `config.py` 的 `Settings` 只有 `dashscope_api_key` 和 `embedding_model` 两个字段。
- `config_router.py` 的 API 只支持读写 `dashscope_api_key`。
- `Settings.tsx` 只有一个 API Key 输入框。

## 技术方案

LiteLLM SDK 模式（不是 Proxy）。LiteLLM 已作为依赖存在，不需要新增安装。

LiteLLM 通过 `provider/model` 前缀区分不同厂商：
- `dashscope/qwen-plus`
- `openai/gpt-4o`
- `deepseek/deepseek-chat`
- `ollama/llama2`
- `openrouter/openai/gpt-4o`

embedding 同理：
- `dashscope/text-embedding-v4`
- `openai/text-embedding-3-small`

我们只需要把硬编码的 provider/model/key/base_url 替换成从配置读取。

## 配置项设计

### config.json（用户运行时配置）存储结构

```json
{
  "llm_provider": "dashscope",
  "llm_model": "qwen-plus",
  "llm_api_key": "sk-xxx",
  "llm_base_url": "",
  "embedding_provider": "dashscope",
  "embedding_model": "text-embedding-v4",
  "embedding_api_key": "",
  "embedding_base_url": ""
}
```

规则：
- `llm_api_key` 为空字符串时，fallback 到 `embedding_api_key`，再 fallback 到旧的 `dashscope_api_key`（向后兼容）。
- `llm_base_url` / `embedding_base_url` 为空时，让 LiteLLM 使用默认值（不传 base_url）。
- 保留旧字段 `dashscope_api_key` 的读取能力，但迁移时优先使用新字段。

### Provider 内置配置表（后端硬编码，减少用户输入）

```python
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
```

## 详细改动清单

### 1. app/backend/config.py

**Settings 类扩展字段**：

在现有字段后追加：

```python
    # ── LLM Provider 配置 ──
    llm_provider: str = "dashscope"
    llm_model: str = "qwen-plus"
    llm_api_key: str = ""
    llm_base_url: str = ""  # 空 = 使用 LiteLLM 默认值

    # ── Embedding Provider 配置 ──
    embedding_provider: str = "dashscope"
    embedding_model: str = "text-embedding-v4"
    embedding_api_key: str = ""  # 空 = fallback 到 llm_api_key
    embedding_base_url: str = ""
```

**apply_user_config 扩展**：

覆盖字段列表从 `("dashscope_api_key",)` 扩展为：

```python
    _CONFIG_KEYS = (
        "dashscope_api_key",
        "llm_provider",
        "llm_model",
        "llm_api_key",
        "llm_base_url",
        "embedding_provider",
        "embedding_model",
        "embedding_api_key",
        "embedding_base_url",
    )
```

脱敏规则：key 字段值替换为 `"***"`，其他字段保留原值。

### 2. app/backend/routers/config_router.py

**ConfigResponse 扩展**：

```python
class ConfigResponse(BaseModel):
    # 旧字段（向后兼容）
    dashscope_api_key: str = ""
    has_api_key: bool = False

    # LLM
    llm_provider: str = "dashscope"
    llm_model: str = "qwen-plus"
    llm_api_key: str = ""        # 返回时脱敏
    llm_base_url: str = ""
    has_llm_key: bool = False

    # Embedding
    embedding_provider: str = "dashscope"
    embedding_model: str = "text-embedding-v4"
    embedding_api_key: str = ""  # 返回时脱敏
    embedding_base_url: str = ""
    has_embedding_key: bool = False
```

`has_api_key` / `has_llm_key` / `has_embedding_key` 逻辑：
- 先读 config.json，再 fallback 到 Settings（环境变量/.env）。
- `has_llm_key` = bool(llm_api_key or dashscope_api_key)
- `has_embedding_key` = bool(embedding_api_key or llm_api_key or dashscope_api_key)

**ConfigUpdate 扩展**：

所有新字段都加入，类型 `str | None = None`，None 表示不更新。

### 3. app/backend/agent/llm_router.py

**核心改造**：`_litellm_model()` 和调用点。

当前代码：
```python
def _litellm_model(task_type: TaskType) -> str:
    model = _ROUTE_MAP.get(task_type, "qwen-plus")
    return f"dashscope/{model}"
```

改造后：

```python
# 任务类型 → 模型名（不含 provider 前缀）
_ROUTE_MAP: dict[str, str] = {
    "general_chat": "qwen-plus",
    "career_planning": "qwen-plus",
    "resume_optimize": "qwen-plus",
    "skill_analysis": "qwen-plus",
    "path_generation": "qwen-plus",
    "memory_summarize": "qwen-plus",
    "embedding": "text-embedding-v4",
}

def _get_model_identifier(task_type: TaskType) -> str:
    """返回 LiteLLM 格式的 model identifier：provider/model"""
    settings = get_settings()
    if task_type == "embedding":
        provider = settings.embedding_provider or settings.llm_provider or "dashscope"
        model = settings.embedding_model or _ROUTE_MAP.get(task_type, "text-embedding-v4")
        # 某些 provider 的 embedding 不需要 provider 前缀（如 openai）
        return f"{provider}/{model}" if provider != "openai" else model
    else:
        provider = settings.llm_provider or "dashscope"
        model = settings.llm_model or _ROUTE_MAP.get(task_type, "qwen-plus")
        return f"{provider}/{model}"

def _get_api_key(for_embedding: bool = False) -> str:
    """获取 API Key，按优先级：专用 key → LLM key → 旧 dashscope key"""
    settings = get_settings()
    if for_embedding and settings.embedding_api_key:
        return settings.embedding_api_key
    if settings.llm_api_key:
        return settings.llm_api_key
    return settings.dashscope_api_key

def _get_base_url(for_embedding: bool = False) -> str | None:
    """获取 base_url，空字符串返回 None（让 LiteLLM 用默认值）"""
    settings = get_settings()
    url = settings.embedding_base_url if for_embedding else settings.llm_base_url
    return url or None
```

`chat()` 和 `chat_stream()` 调用处：
- `model=_get_model_identifier(task_type)`
- `api_key=_get_api_key()`
- `base_url=_get_base_url()`

`embed()` 调用处：
- `model=_get_model_identifier("embedding")`
- `api_key=_get_api_key(for_embedding=True)`
- `base_url=_get_base_url(for_embedding=True)`

**注意**：`_ROUTE_MAP` 里的模型名是不带 provider 前缀的。如果用户自定义了 `llm_model`，直接使用用户值，不再查 `_ROUTE_MAP`。

### 4. app/backend/agent/rag.py

RAG 中的 embedding 调用已经通过 `llm_router.embed()`，所以只要 `llm_router.py` 改造完成，这里自动生效。

唯一需要确认：`ingest_user_memory()` 中的 LlamaIndex DashScopeEmbedding 初始化：

```python
Settings.embed_model = DashScopeEmbedding(
    model_name="text-embedding-v4",
    api_key=cfg.dashscope_api_key,
)
```

这段需要改造为使用 LiteLLM embedding：

```python
from app.backend.agent.llm_router import embed as llm_embed

# 不用 DashScopeEmbedding，改用 LiteLLM 的统一 embedding
# LlamaIndex 没有内置 LiteLLM embedding 类，需要自己封装一个小的 embed_model
```

**具体做法**：创建一个 `LiteLLMEmbedding` 适配器（继承 `llama_index.core.base.embeddings.BaseEmbedding`），内部调用 `llm_router.embed()`。

```python
from llama_index.core.base.embeddings.base import BaseEmbedding

class LiteLLMEmbedding(BaseEmbedding):
    async def _aget_query_embedding(self, query: str) -> list[float]:
        from app.backend.agent.llm_router import embed
        return await embed(query)

    def _get_query_embedding(self, query: str) -> list[float]:
        # sync fallback — 在 async lifespan 中不会被调用
        raise NotImplementedError("use async")

    async def _aget_text_embedding(self, text: str) -> list[float]:
        from app.backend.agent.llm_router import embed
        return await embed(text)

    def _get_text_embedding(self, text: str) -> list[float]:
        raise NotImplementedError("use async")
```

然后 `_ensure_settings()` 中替换：
```python
Settings.embed_model = LiteLLMEmbedding()
```

这样 embedding provider 完全走 LiteLLM，不再硬编码 DashScope。

### 5. app/frontend/src/lib/api.ts

扩展 Config 类型：

```typescript
export type Config = {
  // 旧字段
  dashscope_api_key: string;
  has_api_key: boolean;

  // LLM
  llm_provider: string;
  llm_model: string;
  llm_api_key: string;
  llm_base_url: string;
  has_llm_key: boolean;

  // Embedding
  embedding_provider: string;
  embedding_model: string;
  embedding_api_key: string;
  embedding_base_url: string;
  has_embedding_key: boolean;
};

export async function getConfig(): Promise<Config> {
  return http<Config>("/api/config");
}

export async function updateConfig(data: Partial<Omit<Config, "has_api_key" | "has_llm_key" | "has_embedding_key">>): Promise<Config> {
  return http<Config>("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}
```

### 6. app/frontend/src/pages/Settings.tsx

**UI 布局**：保持现有两卡片结构，但内容扩展。

**卡片 1：AI 模型配置**

分为两栏或上下结构：

**LLM 区域**：
- Provider 下拉选择框：`dashscope`（默认）/ `openai` / `deepseek` / `anthropic` / `gemini` / `ollama` / `openrouter` / `custom`
- Model 输入框：根据 Provider 显示 placeholder 提示（如 `qwen-plus`），用户可自定义输入
- API Key 密码输入框
- Base URL 输入框（选 `custom` 时必填，其他 Provider 可选覆盖）
- 「测试连接」按钮：调用 `POST /api/config/test` 发一个 "hi" 看是否返回 200

**Embedding 区域**：
- 复用 LLM Key 复选框：`使用 LLM 的 Key`（默认勾选）
- Provider 下拉：默认与 LLM 同步，但可独立选择
- Model 输入框
- API Key 密码输入框（复用 LLM Key 时隐藏/disable）
- Base URL 输入框

**卡片 2：数据存储**
保持不变。

**前端 Provider 配置表（与后端同步）**：

```typescript
const PROVIDER_CONFIG: Record<string, { name: string; baseUrl: string; models: string[]; embeddingModels: string[] }> = {
  dashscope: { name: "DashScope（阿里云）", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", models: ["qwen-plus", "qwen-max", "qwen-turbo"], embeddingModels: ["text-embedding-v4"] },
  openai: { name: "OpenAI", baseUrl: "", models: ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"], embeddingModels: ["text-embedding-3-small"] },
  deepseek: { name: "DeepSeek", baseUrl: "https://api.deepseek.com/v1", models: ["deepseek-chat", "deepseek-reasoner"], embeddingModels: [] },
  anthropic: { name: "Anthropic", baseUrl: "", models: ["claude-3-5-sonnet-20241022"], embeddingModels: [] },
  gemini: { name: "Gemini（Google）", baseUrl: "", models: ["gemini-1.5-pro"], embeddingModels: ["models/text-embedding-004"] },
  ollama: { name: "Ollama（本地）", baseUrl: "http://localhost:11434", models: ["llama3.1", "qwen2.5"], embeddingModels: ["nomic-embed-text"] },
  openrouter: { name: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1", models: ["openai/gpt-4o"], embeddingModels: [] },
  custom: { name: "自定义（OpenAI-Compatible）", baseUrl: "", models: [], embeddingModels: [] },
};
```

交互逻辑：
- 切换 Provider 时，自动填充默认 base_url 和推荐 model（如果当前 model 为空或不属于该 provider 的推荐列表）。
- 「测试连接」按钮：POST `/api/config/test`，body `{ provider, model, api_key, base_url }`，后端用 LiteLLM 发一条 "hi"，成功返回 `{ ok: true, latency_ms: 123 }`，失败返回 `{ ok: false, error: "..." }`。

### 7. app/backend/routers/config_router.py（新增 test 接口）

```python
class ConfigTestRequest(BaseModel):
    provider: str
    model: str
    api_key: str
    base_url: str = ""

class ConfigTestResponse(BaseModel):
    ok: bool
    latency_ms: int = 0
    error: str = ""

@router.post("/config/test", response_model=ConfigTestResponse)
async def test_config(body: ConfigTestRequest):
    import time
    from app.backend.agent.llm_router import chat
    start = time.time()
    try:
        model = f"{body.provider}/{body.model}"
        await chat(
            "general_chat",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.7,
            max_tokens=10,
            api_key=body.api_key,
            base_url=body.base_url or None,
        )
        return ConfigTestResponse(ok=True, latency_ms=int((time.time() - start) * 1000))
    except Exception as e:
        return ConfigTestResponse(ok=False, error=str(e)[:200])
```

注意：`chat()` 函数需要支持可选的 `api_key` 和 `base_url` 参数（覆盖全局配置），否则测试接口无法使用临时参数。

改造 `llm_router.py` 的 `chat()` / `chat_stream()` / `embed()` 签名：
```python
async def chat(
    task_type: TaskType,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 2048,
    retries: int = 2,
    *,
    api_key: str | None = None,      # 可选覆盖
    base_url: str | None = None,     # 可选覆盖
    model: str | None = None,        # 可选覆盖（完整 model identifier）
) -> str:
```

当参数为 None 时，fallback 到 `get_settings()` 读取全局配置。

### 8. run.bat / run.ps1（可选）

无需改动。前端 Vite 和后端 FastAPI 的启动方式不变。

## 向后兼容

1. **旧用户**：已有 `dashscope_api_key` 存入 `config.json`。新代码读取时，`llm_api_key` 为空 → fallback 到 `dashscope_api_key`，行为完全一致。
2. **环境变量**：`.env` 中的 `DASHSCOPE_API_KEY` 仍通过 `Settings.dashscope_api_key` 生效，fallback 链保证它被使用。
3. **前端**：Settings 页面加载旧 config 时，新字段为空 → 显示默认 Provider（dashscope）和默认 model（qwen-plus）。

## 验收标准

- [ ] 不填任何新配置，现有行为不变（DashScope qwen-plus）。
- [ ] Settings 页面可切换 Provider 为 DeepSeek，填 key，保存后对话走 DeepSeek。
- [ ] Settings 页面「测试连接」按钮能正确验证各 Provider 连通性。
- [ ] 切换 Provider 后，embedding 也同步切换（如 OpenAI text-embedding-3-small）。
- [ ] Ollama（本地）Provider 可选，base_url 默认 localhost:11434。
- [ ] 重启后端后，配置从 `config.json` 恢复，无需重新填写。
- [ ] `scripts/import_knowledge_base.py`（如有恢复）或 RAG 功能不受影响。

## 文件改动清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `app/backend/config.py` | 修改 | Settings 类扩展 8 个新字段；apply_user_config 扩展覆盖字段列表 |
| `app/backend/routers/config_router.py` | 修改 | ConfigResponse/ConfigUpdate 扩展；新增 `/config/test` 接口 |
| `app/backend/agent/llm_router.py` | 重写 | 移除硬编码 DashScope；`_litellm_model` → `_get_model_identifier`；chat/chat_stream/embed 支持参数覆盖 |
| `app/backend/agent/rag.py` | 修改 | `_ensure_settings()` 用 LiteLLMEmbedding 替代 DashScopeEmbedding |
| `app/frontend/src/lib/api.ts` | 修改 | Config 类型扩展；updateConfig 参数扩展 |
| `app/frontend/src/pages/Settings.tsx` | 重写 | Provider 下拉 + Model 输入 + Key 输入 + Base URL + 测试连接按钮 |
