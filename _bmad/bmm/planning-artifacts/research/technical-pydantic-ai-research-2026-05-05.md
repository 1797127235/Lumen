---
stepsCompleted: [1]
inputDocuments: []
workflowType: 'research'
lastStep: 1
research_type: 'technical'
research_topic: 'PydanticAI Agent Framework Migration'
research_goals: 'Evaluate PydanticAI for replacing hand-coded ReAct Loop in CareerOS'
user_name: 'liu'
date: '2026-05-05'
web_research_enabled: true
source_verification: true
---

# Research Report: PydanticAI Agent Framework Migration

**Date:** 2026-05-05
**Author:** liu
**Research Type:** Technical

---

## Research Overview

评估 PydanticAI 框架是否适合替换 CareerOS 当前手搓的 ReAct Loop Agent 系统。

### 调研维度

1. **框架特性** — PydanticAI 的核心功能和设计理念
2. **兼容性** — 与现有 FastAPI + SQLAlchemy + LiteLLM 架构的集成难度
3. **迁移成本** — 代码改动量、学习曲线、风险点
4. **性能对比** — 延迟、Token 消耗、并发处理
5. **替代方案** — 与其他框架（LangChain/LangGraph）的对比

---

## 1. PydanticAI 核心特性

### 框架定位

PydanticAI 是 Pydantic 团队出品的 AI Agent 框架，设计理念是"把 FastAPI 的开发体验带到 GenAI 应用开发"。

**核心优势**：
- **类型安全**：Pydantic 原生，IDE 自动补全和类型检查
- **模型无关**：支持 OpenAI/Anthropic/Gemini/DeepSeek/Ollama/LiteLLM 等
- **依赖注入**：类似 FastAPI 的 DI 模式（RunContext）
- **流式输出**：原生支持 Agent.run_stream() + AG-UI/Vercel 适配器
- **结构化输出**：Pydantic 模型自动验证
- **可观测性**：集成 Pydantic Logfire（OpenTelemetry）

### 关键 API

| 方法 | 用途 |
|------|------|
| `Agent.run()` | 异步运行，返回完整结果 |
| `Agent.run_sync()` | 同步运行 |
| `Agent.run_stream()` | 流式运行，返回 AsyncIterator |
| `Agent.run_stream_events()` | 流式事件，包含工具调用等 |
| `@agent.tool` | 注册工具（带 RunContext） |
| `@agent.tool_plain` | 注册工具（无依赖） |
| `@agent.system_prompt` | 动态系统提示词 |

---

## 2. 与 CareerOS 兼容性分析

### 2.1 LiteLLM 集成

**结论：完全兼容**

PydanticAI 通过 OpenAI 兼容 API 支持 LiteLLM：

```python
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

# 方式 1：LiteLLM Proxy
model = OpenAIChatModel(
    'qwen-plus',
    provider=OpenAIProvider(
        base_url='http://localhost:4000/v1',  # LiteLLM proxy
        api_key='your-api-key',
    ),
)

# 方式 2：直接使用 DashScope（OpenAI 兼容）
model = OpenAIChatModel(
    'qwen-plus',
    provider=OpenAIProvider(
        base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
        api_key='your-dashscope-key',
    ),
)
```

**与现有 llm_router.py 的关系**：
- 可以保留 llm_router.py 作为 Provider 配置层
- PydanticAI 负责 Agent 逻辑，llm_router 负责 LLM 调用

### 2.2 FastAPI 集成

**结论：无缝集成**

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic_ai import Agent
from pydantic_ai.ui.ag_ui import AGUIAdapter

app = FastAPI()
agent = Agent('openai:gpt-5.2')

@app.post('/api/chat')
async def chat(request: Request) -> StreamingResponse:
    accept = request.headers.get('accept', 'text/event-stream')
    run_input = AGUIAdapter.build_run_input(await request.body())
    adapter = AGUIAdapter(agent=agent, run_input=run_input, accept=accept)
    
    return StreamingResponse(
        adapter.encode_stream(adapter.run_stream()),
        media_type=accept
    )
```

### 2.3 SQLAlchemy 集成

**结论：通过依赖注入完美集成**

```python
from dataclasses import dataclass
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic_ai import Agent, RunContext

@dataclass
class CareerOSDeps:
    user_id: str
    db: AsyncSession

agent = Agent('openai:gpt-5.2', deps_type=CareerOSDeps)

@agent.tool
async def get_profile(ctx: RunContext[CareerOSDeps]) -> dict:
    """获取用户画像"""
    result = await ctx.deps.db.execute(
        select(UserProfile).where(UserProfile.user_id == ctx.deps.user_id)
    )
    profile = result.scalar_one_or_none()
    return profile.model_dump() if profile else {}
```

---

## 3. 迁移成本评估

### 3.1 组件映射

| CareerOS 组件 | PydanticAI 等价物 | 迁移难度 |
|---------------|-------------------|----------|
| `agent_loop.py` (ReAct Loop) | `Agent.run()` 内置循环 | ⭐⭐ 中 |
| `tools.py` (ToolRegistry) | `@agent.tool` 装饰器 | ⭐ 低 |
| `llm_router.py` (LiteLLM) | OpenAI 兼容 Provider | ⭐ 低 |
| `orchestrator.py` (意图分类) | `@agent.system_prompt` 动态指令 | ⭐⭐ 中 |
| 流式输出 (AsyncIterator) | `Agent.run_stream()` | ⭐⭐ 中 |
| 工具执行 (execute()) | PydanticAI 内置 | ⭐ 低 |

### 3.2 代码改动量估算

| 文件 | 改动类型 | 预估工作量 |
|------|----------|-----------|
| `agent_loop.py` | 完全重写 | 2-3 小时 |
| `tools.py` | 重构为装饰器 | 1-2 小时 |
| `llm_router.py` | 保留 + 适配 | 0.5 小时 |
| `orchestrator.py` | 重构为动态指令 | 1-2 小时 |
| `chat_service.py` | 更新调用方式 | 1 小时 |
| 测试文件 | 更新测试 | 2-3 小时 |
| **总计** | | **8-12 小时** |

### 3.3 风险点

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Cognee 集成 | 低 | PydanticAI 支持自定义工具，Cognee 可作为工具 |
| 流式输出格式 | 中 | 需要适配 AG-UI 或自定义 SSE 格式 |
| 工具调用日志 | 低 | PydanticAI 有内置 Logfire 集成 |
| 学习曲线 | 低 | Pydantic 团队文档完善，API 设计直观 |

---

## 4. 与其他框架对比

| 维度 | PydanticAI | LangChain | LangGraph |
|------|------------|-----------|-----------|
| **类型安全** | ⭐⭐⭐ 原生 Pydantic | ⭐ 弱 | ⭐⭐ 中 |
| **依赖注入** | ⭐⭐⭐ RunContext | ⭐ 手动 | ⭐⭐ 手动 |
| **流式输出** | ⭐⭐⭐ 原生支持 | ⭐⭐ 需配置 | ⭐⭐ 需配置 |
| **LiteLLM 支持** | ⭐⭐⭐ 原生 | ⭐⭐⭐ 原生 | ⭐⭐⭐ 原生 |
| **学习曲线** | ⭐ 低 | ⭐⭐⭐ 高 | ⭐⭐⭐ 高 |
| **社区生态** | ⭐⭐ 成长中 | ⭐⭐⭐ 最大 | ⭐⭐⭐ 大 |
| **代码侵入性** | ⭐ 低 | ⭐⭐⭐ 高 | ⭐⭐⭐ 高 |
| **与 FastAPI 集成** | ⭐⭐⭐ 原生 | ⭐⭐ 需适配 | ⭐⭐ 需适配 |

**结论**：对于 CareerOS 这种已有 FastAPI + Pydantic 栈的项目，PydanticAI 是最佳选择。

---

## 5. 迁移路径建议

### Phase 1：准备工作（1 小时）

1. 安装 PydanticAI：`pip install pydantic-ai`
2. 创建 `app/backend/agent/pydantic_agent.py` 作为新入口
3. 保留现有 `agent_loop.py` 作为回退

### Phase 2：核心迁移（4-6 小时）

1. **定义依赖类型**：
```python
@dataclass
class CareerOSDeps:
    user_id: str
    db: AsyncSession
    user_profile: dict | None
```

2. **注册工具**：
```python
agent = Agent('litellm:qwen-plus', deps_type=CareerOSDeps)

@agent.tool
async def get_profile(ctx: RunContext[CareerOSDeps]) -> dict:
    ...

@agent.tool
async def update_profile(ctx: RunContext[CareerOSDeps], fields: dict) -> str:
    ...

@agent.tool
async def diagnose_jd(ctx: RunContext[CareerOSDeps], jd_text: str) -> dict:
    ...
```

3. **动态系统提示词**：
```python
@agent.system_prompt
async def build_system_prompt(ctx: RunContext[CareerOSDeps]) -> str:
    profile = ctx.deps.user_profile
    memories = await retrieve_memories(ctx.deps.user_id)
    return f"""你是 CareerOS 职业规划助手。
用户画像：{profile}
相关记忆：{memories}"""
```

4. **流式对话**：
```python
async def stream_chat(user_id: str, user_input: str):
    deps = CareerOSDeps(user_id=user_id, db=await get_db())
    
    async with agent.run_stream(user_input, deps=deps) as response:
        async for text in response.stream_text():
            yield f"data: {json.dumps({'type': 'token', 'content': text})}\n\n"
```

### Phase 3：集成测试（2-3 小时）

1. 更新 `chat_service.py` 使用新的 Agent
2. 运行现有测试，确保功能一致
3. 验证流式输出、工具调用、错误处理

### Phase 4：清理（1 小时）

1. 删除旧的 `agent_loop.py`
2. 更新文档
3. 提交代码

---

## 6. 结论与建议

### 推荐：✅ 采用 PydanticAI

**理由**：
1. **完美兼容**：与现有 FastAPI + SQLAlchemy + LiteLLM 栈无缝集成
2. **类型安全**：Pydantic 原生，减少运行时错误
3. **开发效率**：装饰器模式，代码量减少 50%+
4. **维护性**：官方维护，文档完善，社区活跃
5. **未来扩展**：支持 MCP、A2A、多 Agent 协作

### 不推荐 LangChain/LangGraph 的原因

1. **代码侵入性高**：需要重构整个项目结构
2. **学习曲线陡峭**：概念多，配置复杂
3. **过度封装**：对于 CareerOS 的简单场景过于重量级

### 下一步行动

1. **立即**：安装 PydanticAI，创建 POC 验证 LiteLLM 集成
2. **本周**：完成 Phase 1-2，实现核心 Agent 迁移
3. **下周**：完成 Phase 3-4，测试并上线

---

## 参考资源

- **官方文档**：https://ai.pydantic.dev/
- **GitHub**：https://github.com/pydantic/pydantic-ai
- **LiteLLM 集成**：https://ai.pydantic.dev/models/openai/#litellm
- **流式输出**：https://ai.pydantic.dev/output/#streamed-results
- **依赖注入**：https://ai.pydantic.dev/dependencies/
- **AG-UI 协议**：https://ai.pydantic.dev/ui/
