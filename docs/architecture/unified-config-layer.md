# 统一配置层方案

> 日期：2026-05-29
> 状态：草案
> 作者：Sisyphus

## 背景

当前 Lumen 的模型配置分散在多个位置，存在以下问题：

1. **TUI `/model` 命令不持久化**：只修改内存状态，重启后丢失
2. **配置字段命名不一致**：`llm_context_window` vs `llm_context_limit`
3. **Agent 缓存不及时更新**：修改 api_key/base_url 后需重启
4. **`_create_model()` 不读 providers 配置**：供应商页面配置可能不生效
5. **配置来源过多**：7 个地方都可能有模型配置，优先级不清

## 目标

建立统一的配置管理层，确保：

- **单一真相源**：`config.json` 是唯一的持久化存储
  - `.env` 仅作启动默认值，提供初始 provider/model/api_key/base_url
  - 用户修改后写入 `config.json`，覆盖 `.env` 的值
  - 两者是 bootstrap + override 关系，不是互斥关系
- **单一写入点**：所有写入必须通过 `POST /api/config`
- **单一读取点**：所有读取必须通过 `get_settings()`
- **变更通知**：`config.json` 变更后自动刷新 Settings

## 现状分析

### 当前配置来源（7 个）

```
┌─────────────────────────────────────────────────────────────┐
│  1. .env 文件                                                │
│     LLM_PROVIDER=dashscope                                  │
│     LLM_MODEL=qwen-plus                                     │
│     LLM_API_KEY=sk-xxx                                      │
│     LLM_BASE_URL=https://...                                │
└─────────────────────────────────────────────────────────────┘
                            ↓ 启动时加载
┌─────────────────────────────────────────────────────────────┐
│  2. Settings 单例 (core/config.py)                           │
│     settings.llm_provider                                   │
│     settings.llm_model                                      │
│     settings.llm_api_key                                    │
│     settings.llm_base_url                                   │
└─────────────────────────────────────────────────────────────┘
                            ↓ apply_user_config() 覆盖
┌─────────────────────────────────────────────────────────────┐
│  3. config.json (~/.lumen/config.json)                       │
│     {                                                        │
│       "llm_provider": "dashscope",                          │
│       "llm_model": "qwen-max",                              │
│       "llm_api_key": "***",                                 │
│       "providers": {                                         │
│         "dashscope": { "api_key": "...", "base_url": "..." }│
│       }                                                      │
│     }                                                        │
└─────────────────────────────────────────────────────────────┘
                            ↓ API 读取
┌─────────────────────────────────────────────────────────────┐
│  4. 前端/ TUI 运行时状态                                      │
│     - Web: React state                                      │
│     - TUI: local.model.set() (内存，不持久化)                 │
└─────────────────────────────────────────────────────────────┘
                            ↓ 发送请求时
┌─────────────────────────────────────────────────────────────┐
│  5. 请求级覆盖                                               │
│     POST /api/chat { provider: "...", model: "..." }        │
│     → InboundMessage(provider=..., model=...)               │
└─────────────────────────────────────────────────────────────┘
                            ↓ Agent 选择
┌─────────────────────────────────────────────────────────────┐
│  6. Agent 缓存                                              │
│     LumenAgent.get(provider, model)                         │
│     → 缓存 key: "provider|model|tools_fp"                   │
└─────────────────────────────────────────────────────────────┘
                            ↓ 创建模型
┌─────────────────────────────────────────────────────────────┐
│  7. _create_model() 最终读取                                 │
│     api_key: settings.llm_api_key (不读 providers 配置!)     │
│     base_url: settings.llm_base_url                         │
└─────────────────────────────────────────────────────────────┘
```

### 现有问题清单

| 问题 | 严重程度 | 位置 | 说明 |
|------|----------|------|------|
| TUI /model 不持久化 | 🔴 高 | `channels/cli/cmd/tui/component/prompt/index.tsx` | 只调用 `local.model.set()`，不写 config.json |
| 字段命名不一致 | 🟡 中 | `server/routes/config.py` vs `core/config.py` | `llm_context_window` vs `llm_context_limit` |
| Agent 缓存不更新 | 🟡 中 | `core/agent.py` | api_key/base_url 变更后缓存不失效 |
| 不读 providers 配置 | 🟡 中 | `core/agent.py` `_create_model()` | 供应商页面配置的 key 不生效 |
| TUI 缺少 updateConfig | 🔴 高 | `channels/cli/cmd/tui/lumen/api.ts` | 无 POST /api/config 方法 |

## 目标架构

```
┌─────────────────────────────────────────────────────────────┐
│  写入流（统一）                                               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Web 前端 ─────────┐                                        │
│                    │                                        │
│  TUI /model ───────┼──→ POST /api/config ──→ config.json   │
│                    │              ↓                         │
│  TUI /vl (未来) ───┘      apply_user_config()              │
│                                ↓                           │
│                         Settings 单例刷新                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  读取流（统一）                                               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  config.json ──→ apply_user_config() ──→ Settings 单例      │
│                                              ↓              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Settings 单例（运行时缓存）                          │   │
│  │  - llm_provider                                     │   │
│  │  - llm_model                                        │   │
│  │  - llm_api_key                                      │   │
│  │  - llm_base_url                                     │   │
│  │  - llm_context_limit                                │   │
│  └─────────────────────────────────────────────────────┘   │
│                    │              │              │          │
│                    ↓              ↓              ↓          │
│              Agent 创建     Web GET /api/config  TUI 读取   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 改造方案

### 1. 修复字段命名不一致

**文件**：`server/routes/config.py`

```python
# 修改前
user_ctx = user_config.get("llm_context_window")

# 修改后
user_ctx = user_config.get("llm_context_limit")
```

**文件**：`core/config.py`

```python
# 确保 Settings 使用 llm_context_limit
class Settings(BaseSettings):
    llm_context_limit: int = 0  # 已存在，无需修改
```

**迁移策略**：读取时兼容旧字段名

```python
# server/routes/config.py - get_config()
user_ctx = user_config.get("llm_context_limit") or user_config.get("llm_context_window")
```

### 2. TUI 添加 updateConfig()

**文件**：`channels/cli/cmd/tui/lumen/api.ts`

```typescript
export async function updateConfig(data: {
  llm_provider?: string
  llm_model?: string
  llm_api_key?: string
  llm_base_url?: string
}): Promise<LumenConfig> {
  const res = await fetch(`${BASE_URL}/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}
```

### 3. 修改 /model 命令 - 持久化

**文件**：`channels/cli/cmd/tui/component/prompt/index.tsx`

```typescript
// 现有代码（约 1183-1190 行）
case "model_set":
  // 直接设置模型
  if (result.model) {
    local.model.set({
      providerID: result.provider ?? local.model.current().providerID,
      modelID: result.model,
    })
  }
  break

// 修改后
case "model_set":
  if (result.model) {
    const providerID = result.provider ?? local.model.current().providerID
    const modelID = result.model
    
    // 1. 更新内存状态
    local.model.set({ providerID, modelID })
    
    // 2. 持久化到 config.json
    LumenApi.updateConfig({
      llm_provider: providerID,
      llm_model: modelID,
    }).catch((err) => {
      toast.show({ 
        message: `配置保存失败: ${err.message}`, 
        variant: "error" 
      })
    })
  }
  break
```

### 4. 修复 _create_model() 读取 providers 配置

**文件**：`core/agent.py`

**前置条件**：确保文件顶部已导入 `load_user_config`

```python
# core/agent.py 顶部
from core.config import get_settings, load_user_config  # 确保导入 load_user_config
```

```python
def _create_model(
    self,
    provider: str | None = None,
    model: str | None = None,
) -> OpenAIChatModel:
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    settings = get_settings()
    provider = provider or settings.llm_provider
    model_name = model or settings.llm_model
    
    # 优先从 providers 配置读取 key 和 base_url
    user_cfg = load_user_config()
    provider_cfg = (user_cfg.get("providers") or {}).get(provider, {})
    provider_key = provider_cfg.get("api_key", "")
    provider_base_url = provider_cfg.get("base_url", "")
    
    # 解析优先级：providers 配置 > Settings > 旧字段
    api_key = (
        provider_key
        or settings.llm_api_key 
        or settings.dashscope_api_key
        or ""
    )
    base_url = (
        provider_base_url
        or settings.llm_base_url
        or ""
    )

    if not api_key:
        raise ValueError(
            "未配置 LLM API Key。请在设置页面配置 API Key，"
            "或在 .env 文件中设置 DASHSCOPE_API_KEY 或 LLM_API_KEY。"
        )
    if not base_url:
        raise ValueError(
            f"未配置 LLM Base URL。请在设置页面配置 Base URL，"
            f"或在 .env 文件中设置 LLM_BASE_URL（当前 provider: {provider}）。"
        )

    logger.info(
        "创建模型",
        provider=provider,
        model=model_name,
        base_url=base_url,
        has_key=bool(api_key),
    )

    return OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(base_url=base_url, api_key=api_key),
    )
```

> **注意**：pydantic_ai 1.102 中类名是 `OpenAIChatModel`，不是 `OpenAIModel`。现有代码 `core/agent.py:10,206,228` 都使用 `OpenAIChatModel`。

### 5. Agent 缓存失效机制

**文件**：`core/agent.py`

**前置条件**：在文件顶部添加 `import hashlib` 和 `import json`

```python
# core/agent.py 顶部
import hashlib  # 新增：用于配置指纹计算
import json     # 新增：用于序列化 providers 配置
```

```python
class LumenAgent:
    def __init__(self) -> None:
        self._agents: dict[str, Agent[LumenDeps, str]] = {}
        self._generation: int = 0
        self._config_fingerprint: str = ""  # 新增

    def _get_config_fingerprint(self) -> str:
        """计算配置指纹，变更时自动失效缓存
        
        包含：
        - Settings 中的顶层 key（兼容 .env）
        - config.json 中的 providers 配置（供应商页面配置）
        """
        settings = get_settings()
        user_cfg = load_user_config()
        
        parts = [
            settings.llm_api_key,
            settings.llm_base_url,
            settings.dashscope_api_key,
            # 关键：包含 providers 配置，否则供应商页面改 key 不会触发缓存失效
            json.dumps(user_cfg.get("providers") or {}, sort_keys=True),
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

    def get(
        self,
        provider: str | None = None,
        model: str | None = None,
    ) -> Agent[LumenDeps, str]:
        """返回对应 (provider, model) 的缓存 Agent；工具变化时清旧缓存。"""
        s = get_settings()
        eff_provider = provider or s.llm_provider
        eff_model = model or s.llm_model
        tools_fp = self._tool_fingerprint()
        key = f"{eff_provider}|{eff_model}|{tools_fp}"

        # 配置变更时清空所有缓存
        current_fp = self._get_config_fingerprint()
        if current_fp != self._config_fingerprint:
            logger.info("配置变更，清空 Agent 缓存")
            self._agents.clear()
            self._config_fingerprint = current_fp

        if key not in self._agents:
            from lib.tools.factory import register_all_tools
            register_all_tools()
            self._agents = {k: v for k, v in self._agents.items() if k.endswith(tools_fp)}
            self._agents[key] = self.create(eff_provider, eff_model)
            self._generation += 1
            logger.info(
                "Agent 已构建",
                provider=eff_provider,
                model=eff_model,
                generation=self._generation,
            )

        return self._agents[key]
```

> **注意**：现有 `_tool_fingerprint()` 方法（line 242）已经使用 `hashlib.sha256`，但包在 `try/except` 里，`NameError` 被吞，永远返回 `"v1"`。这意味着 MCP 工具增删目前不会触发 Agent 重建，是一个潜伏 bug。建议同时修复 `_tool_fingerprint()` 的 import 问题。

### 6. TUI 启动时同步配置（无需改动）

**文件**：`channels/cli/cmd/tui/context/local.tsx`

现有逻辑已经是正确的：

```typescript
// 启动时从 GET /api/config 读取
const config = await LumenApi.getConfig()
// 设置到本地状态
```

## 改造后的数据流

### 写入流

```
用户操作
    ↓
┌─────────────────────────────────────────────────────────────┐
│  Web 前端                                                   │
│  - Settings 页面修改                                         │
│  - 调用 updateConfig()                                      │
│  - POST /api/config                                         │
└─────────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────────┐
│  TUI                                                        │
│  - /model qwen-max                                          │
│  - 后端返回 model_set action                                │
│  - TUI 调用 updateConfig()                                  │
│  - POST /api/config                                         │
└─────────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────────┐
│  后端 API                                                    │
│  - save_user_config(data)                                   │
│  - 写入 ~/.lumen/config.json                                │
│  - apply_user_config(get_settings(), merged)                │
│  - Settings 单例刷新                                         │
└─────────────────────────────────────────────────────────────┘
```

### 读取流

```
消费者
    ↓
┌─────────────────────────────────────────────────────────────┐
│  Agent 创建                                                  │
│  - get_settings() 读取 Settings 单例                         │
│  - load_user_config() 读取 providers 配置                    │
│  - 合并后创建 OpenAIModel                                    │
└─────────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────────┐
│  Web 前端                                                    │
│  - GET /api/config                                          │
│  - 从 Settings 单例读取                                      │
│  - 返回给前端                                                │
└─────────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────────┐
│  TUI                                                        │
│  - getConfig()                                              │
│  - GET /api/config                                          │
│  - 从 Settings 单例读取                                      │
│  - 返回给 TUI                                               │
└─────────────────────────────────────────────────────────────┘
```

## 改造优先级

| 序号 | 任务 | 文件 | 影响 | 预估工时 |
|------|------|------|------|----------|
| 1 | 修复字段命名不一致 | `server/routes/config.py` | 基础 | 10 min |
| 2 | TUI 添加 updateConfig() | `channels/cli/cmd/tui/lumen/api.ts` | 前置条件 | 10 min |
| 3 | /model 命令持久化 | `channels/cli/cmd/tui/component/prompt/index.tsx` | 用户体验 | 15 min |
| 4 | _create_model() 读取 providers 配置 | `core/agent.py` | 功能正确性 | 20 min |
| 5 | Agent 缓存失效机制 | `core/agent.py` | 稳定性 | 15 min |

**总预估工时**：约 70 分钟

## 测试验证

### 测试场景 1：TUI /model 持久化

```bash
# 1. 在 TUI 中切换模型
/model qwen-max

# 2. 重启 TUI

# 3. 验证模型是否仍然是 qwen-max
```

### 测试场景 2：Web 设置页同步

```bash
# 1. 在 TUI 中切换模型
/model qwen-max

# 2. 打开 Web 设置页

# 3. 验证显示的模型是否是 qwen-max
```

### 测试场景 3：供应商配置生效

```bash
# 1. 在 Web 设置页配置供应商 API Key

# 2. 发送消息

# 3. 验证 Agent 是否使用了供应商配置的 Key
```

### 测试场景 4：配置变更后 Agent 刷新

```bash
# 1. 发送消息（创建 Agent 缓存）

# 2. 在 Web 设置页修改 API Key

# 3. 再次发送消息

# 4. 验证 Agent 是否使用了新的 Key
```

## 后续扩展

统一配置层完成后，可以方便地添加：

1. **VL 模型配置**：在 config.json 中添加 `vl_provider`、`vl_model` 等字段
2. **Embedding 模型配置**：已有字段，确保读取路径一致
3. **多模型切换**：支持快速切换预设的模型组合

## 附录：优先级设计说明

### 配置读取优先级

```
providers[provider].api_key  （供应商页面配置，最高优先级）
    ↓ 未配置时
settings.llm_api_key         （顶层字段，来自 .env 或 config.json）
    ↓ 未配置时
settings.dashscope_api_key   （旧字段兼容）
```

### 为什么 providers 优先？

- Web 设置页的「供应商」页面配置的是 `providers[provider].api_key`
- TUI `/model` 和顶层配置写的是 `llm_provider` / `llm_model`
- 如果用户在供应商页面为某个 provider 单独配置了 key，应该优先使用

### 潜在冲突场景

```
用户操作：
1. 在 .env 设置 LLM_API_KEY=key-1
2. 在供应商页面为 dashscope 配置 api_key=key-2
3. 在 TUI 执行 /model dashscope/qwen-max

预期行为：使用 key-2（供应商页面配置）
实际行为：使用 key-2（providers 优先）
```

这个优先级是合理的，因为供应商页面是更细粒度的配置，应该覆盖顶层默认值。

## 参考文档

- [core/config.py](../../core/config.py) - 配置管理
- [core/agent.py](../../core/agent.py) - Agent 创建
- [server/routes/config.py](../../server/routes/config.py) - 配置 API
- [channels/cli/cmd/tui/lumen/api.ts](../../channels/cli/cmd/tui/lumen/api.ts) - TUI API 客户端
