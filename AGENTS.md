# AGENTS.md

## 定位

Lumen — 一个真正认识你的 AI 伴侣。FastAPI + SQLAlchemy + PydanticAI + LiteLLM + SQLite。前端 React 19 + Vite + Tailwind CSS 4。

---

## Tutorial: 第一次跑起来

### 1. 环境准备

- Python 3.11+
- Node.js 18+
- Rust + cargo（仅桌面开发需要）
- Windows PowerShell 或 cmd

### 2. 安装依赖

```bash
# Python
pip install -r requirements.txt

# 前端
npm install
```

### 3. 配置 .env

复制 `.env.example` 为 `.env`，填入至少一项：

```
LLM_API_KEY=sk-xxx          # 或 DASHSCOPE_API_KEY
LLM_BASE_URL=https://...    # 对应 provider 的 base url
LLM_MODEL=gpt-4o            # 或 qwen-max 等
```

### 4. 启动并验证

**Web 开发模式：**
```powershell
.\start.ps1    # PowerShell
# 或双击 start.bat
```
打开 http://localhost:5173

**桌面模式（推荐）：**
```bash
cargo tauri dev
```
Python 后端作为 sidecar 子进程由 Rust 管理生命周期。

**验证后端健康：**
```bash
curl http://localhost:8000/api/health
```

### 5. 发第一条消息

打开前端页面 → 在输入框发送任意消息 → 观察 SSE 流式输出。
如果 Agent 回复正常，说明 LLM 配置正确。

---

## How-to: 常见开发任务

### 添加一个新的 Agent 工具

1. **定义输入类型**：在 `backend/modules/agent/tools/builtin/schemas.py` 新增 `TypedDict`
2. **实现 Handler**：在 `backend/modules/agent/tools/builtin/` 下新建文件或添加到现有文件
3. **注册工具**：在 `backend/modules/agent/tools/core/factory.py` 的 `create_tool_runtime()` 中注册
4. **测试**：重启后端，在对话中触发工具调用

关键约束：
- Handler 签名：`async def handle_xxx(args: dict[str, Any], ctx: ToolRuntimeContext) -> str`
- `ctx.db` 是 AsyncSession，Handler 内部可自由读写数据库
- 工具返回必须是字符串（会被 Agent 读取作为工具执行结果）

### 添加一个新的记忆事件类型

1. **分类决策**：该事件属于 Profile 管线还是 Narrative 管线？
   - Profile（用户画像维度）：写入 `.md` 投影，不进搜索索引，L0 固定注入
   - Narrative（时间线/经历类）：进入 FTS5 + LanceDB 搜索，L2 按需召回

2. **在 `classifier.py` 中注册**：
   - `PROFILE_EVENT_TYPES` 或 `NARRATIVE_EVENT_TYPES` 中加入新类型
   - `L0_FIXED_TYPES` 决定是否 L0 固定注入

3. **事件合并规则**（`events_merger.py`）：
   - 如果同类事件需要合并（如多次提到同一个兴趣），在 `deep_merge_payload()` 中添加合并逻辑
   - 如果不需要合并（如每次独立经历），使用默认 newer-wins

4. **在 Agent 中使用**：通过 `memory_save` 工具或直接调用 `memory.remember()`

### 调试流式对话

1. **查看后端日志**：`DEBUG=true` 时日志输出结构化 JSON，包含 `event_kind`、`tool_name`、`conversation_id`
2. **查看 Agent Trace**：`agent_traces` 表记录每一步推理、工具调用、耗时
3. **检查上下文注入**：`memory/build_context()` 的输出被注入到 system prompt 中，日志中搜索 `build_context` 可查看注入内容
4. **强制刷新 Agent 缓存**：修改工具或配置后，`_tool_runtime` 全局缓存不会自动刷新，需重启后端

### 运行测试

```bash
pytest                    # 全部 104 条测试
pytest -x                # 遇到失败立即停
pytest tests/test_memory_dedup.py -v   # 单文件详细输出
```

### 配置外部数据源

1. 前端打开「设置 → 我的世界」
2. 添加本地文件夹路径（.md/.txt）
3. `IngestionPipeline` 自动扫描 → 解析 → 写入 `external_items` → 同步 FTS5 + LanceDB
4. Agent 对话中用 `scope='knowledge'` 搜索外部文档

---

## Reference

### 项目结构

```
career-os/
├── backend/                    # FastAPI 后端
│   ├── main.py                 # FastAPI 入口，lifespan 中自动建表 + 初始化
│   ├── model_registry.py       # SQLAlchemy 模型注册
│   ├── core/                   # 基础设施
│   │   ├── config.py           # pydantic-settings，从根目录 .env 加载
│   │   ├── db.py               # SQLAlchemy AsyncEngine + Base + get_async_session_maker
│   │   ├── logging.py          # 结构化日志配置
│   │   ├── migrations.py       # SQLite 兼容迁移、FTS5 表、触发器
│   │   └── startup.py          # 启动初始化（建表、IngestionPipeline、DocumentIndexProvider）
│   ├── shared/                 # 跨模块通用工具
│   │   └── path_utils.py       # 路径工具（find_project_root）
│   └── modules/                # 业务模块（按领域拆分）
│       ├── agent/              # Agent 系统
│       │   ├── pydantic_agent.py   # PydanticAI Agent 定义 + 动态系统提示词
│       │   ├── event_handlers.py   # Agent 事件处理（流式输出、工具调用跟踪）
│       │   ├── deps.py             # Agent 依赖注入（LumenDeps）
│       │   ├── models.py           # AgentTrace 可观测性
│       │   └── tools/
│       │       ├── file_security.py     # 文件安全（路径校验、大小限制、二进制检测）
│       │       ├── builtin/             # 内置工具 Handler
│       │       │   ├── memory.py        # memory_search, memory_save
│       │       │   ├── profile.py       # get_profile, update_profile
│       │       │   ├── files.py         # 文件读写工具
│       │       │   ├── external.py      # search_external_docs
│       │       │   └── schemas.py       # 工具输入/输出 Pydantic schema
│       │       ├── core/                # 工具运行时核心
│       │       │   ├── registry.py      # ToolRegistry
│       │       │   ├── dispatcher.py    # ToolDispatcher
│       │       │   ├── definitions.py   # ToolDefinition
│       │       │   ├── factory.py       # ToolRuntime 工厂
│       │       │   ├── context.py       # ToolRuntimeContext
│       │       │   ├── policies.py      # 安全策略
│       │       │   └── toolsets.py      # Toolset 定义
│       │       ├── adapters/
│       │       │   └── pydanticai.py    # PydanticAI 工具适配器
│       │       └── mcp/                 # MCP 预留
│       ├── chat/               # 对话模块
│       │   ├── router.py       # POST /api/chat (SSE), GET /api/chat/history
│       │   ├── service.py      # 对话业务：Agent Loop 集成 + SSE 流式输出
│       │   ├── session.py      # 会话状态管理
│       │   ├── persistence.py  # 消息持久化
│       │   ├── lock.py         # 对话并发锁
│       │   ├── summary.py      # 对话摘要后台任务
│       │   └── models.py       # Conversation + Message
│       ├── memory/             # 记忆层（双管线：Profile + Narrative）
│       │   ├── facade.py       # LumenMemory 统一门面（多继承组合）
│       │   ├── models.py       # GrowthEvent ORM 模型
│       │   ├── classifier.py   # 事件分类（Profile / Narrative / L0 路由）
│       │   ├── writer.py       # 事件写入（单条/批量，含 L1/L2 去重）
│       │   ├── searcher.py     # 搜索/召回 + 上下文构建（L0/L1/L2 分层注入）
│       │   ├── search.py       # 全文搜索（FTS5 + Provider 语义）+ 外部文档搜索
│       │   ├── relational_store.py  # Repository + FTS5 触发器管理
│       │   ├── projection.py   # .md 投影同步、全量重建、删除、重置
│       │   ├── markdown.py     # .md 原子读写 + growth_events → memory.md
│       │   ├── snapshot.py     # Agent 系统提示词分层快照（L0/L1/L2）
│       │   ├── events_merger.py  # 事件合并与 memory.md 生成（纯函数层）
│       │   ├── understanding.py  # AI 综合画像生成（about_you.md + patterns + intents）
│       │   ├── router.py         # 记忆管理 API 路由
│       │   └── review_service.py # 后台记忆审查（Agent fork 审查对话）
│       ├── profile/            # 画像模块
│       │   ├── models.py       # User + UserProfile（通用伴侣画像）
│       │   └── schemas.py      # ProfilePayload, KeyValuePayload, DecisionPayload
│       ├── config/             # 配置模块
│       │   ├── router.py       # GET/POST /api/config, /api/config/providers, /api/config/test
│       │   └── service.py      # 配置业务逻辑
│       ├── health/             # 健康检查
│       │   └── router.py       # GET /api/health
│       └── data_sources/       # 外部数据接入
│           ├── models.py       # DataSource + ExternalItem ORM
│           ├── schemas.py      # 数据源 Pydantic schema
│           ├── registry.py     # DataSource 注册表
│           ├── router.py       # 数据源管理 API
│           ├── service.py      # 数据源业务逻辑
│           └── ingestion/      # 接入管道
│               ├── connector.py
│               ├── pipeline.py
│               ├── store.py
│               ├── parser.py
│               ├── retry.py
│               ├── document_index_provider.py
│               ├── provider_factory.py
│               ├── connectors/
│               │   └── local_folder.py
│               └── providers/
│                   ├── lancedb.py
│                   └── null.py
├── src/                        # React 前端（Vite）
│   ├── App.tsx
│   ├── main.tsx                # 路由配置
│   ├── index.css
│   ├── pages/
│   │   ├── Chat.tsx            # SSE 流式对话 + 历史抽屉 + 思考过程
│   │   ├── Profile.tsx         # AI 综合画像 + 主动塑造 + 模式/心愿/此刻/时间线
│   │   ├── Memories.tsx        # 记忆列表管理
│   │   ├── MyWorld.tsx         # 外部数据源管理（我的世界）
│   │   └── Settings.tsx        # Provider 选择 + API Key + 数据源配置
│   ├── components/
│   │   ├── Card.tsx
│   │   ├── EmptyState.tsx
│   │   ├── ProfileActions.tsx
│   │   └── Sidebar.tsx
│   └── lib/
│       ├── api.ts              # API 客户端统一导出
│       ├── chatSession.tsx     # 全局聊天状态管理
│       ├── userId.ts           # 用户 ID 管理
│       ├── thinkSegments.ts    # 思考片段处理
│       └── api/                # API 模块拆分
│           ├── core.ts
│           ├── chat.ts
│           ├── memory.ts
│           ├── config.ts
│           └── data_sources.ts
├── src-tauri/                  # Tauri v2 桌面壳（Rust）
│   └── src/
│       ├── lib.rs              # start_backend / stop_backend
│       └── main.rs
├── tests/                      # pytest 测试（104 条）
├── docs/                       # 设计文档
│   ├── architecture/           # 系统架构设计
│   ├── memory-structure/       # 记忆结构（memory.md + entities/*.md）
│   ├── stories/                # 功能实现 story 记录
│   ├── blog/                   # 博客文章
│   └── issues/                 # 问题记录
├── .github/workflows/          # CI/CD
├── pyproject.toml              # ruff + pytest 配置
├── start.ps1 / start.bat
└── requirements.txt
```

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | 健康检查（含 DocumentIndexProvider 状态） |
| `POST` | `/api/chat` | SSE 流式对话，body: `{ message, conversation_id?, user_id? }` |
| `GET` | `/api/chat/history?user_id=&limit=` | 对话历史列表 |
| `GET` | `/api/chat/{conversation_id}` | 单条会话消息详情 |
| `DELETE` | `/api/chat/{conversation_id}` | 删除对话及其消息 |
| `GET` | `/api/memory/me` | 读取用户画像记忆内容 |
| `GET` | `/api/memory/stats?user_id=` | 记忆统计 |
| `GET` | `/api/memory/list?user_id=` | 记忆列表 |
| `POST` | `/api/memory/reset?user_id=` | 清空用户记忆 |
| `POST` | `/api/memory/rebuild` | 重建记忆（.md + FTS5 + Provider） |
| `GET` | `/api/memory/search` | 语义搜索记忆 |
| `DELETE` | `/api/memory/{event_id}` | 删除指定记忆事件 |
| `POST` | `/api/memory/tell` | 用户主动告诉 AI（兴趣/价值观/关系/经历/反思） |
| `GET` | `/api/memory/understanding` | 获取 AI 综合画像 |
| `POST` | `/api/memory/understanding/refresh` | 手动触发 AI 画像重新生成 |
| `POST` | `/api/memory/understanding/correct` | 用户手动纠正 AI 画像文本 |
| `GET` | `/api/config` | 获取当前用户配置（含脱敏 key） |
| `POST` | `/api/config` | 更新用户配置 |
| `GET` | `/api/config/providers` | Provider 目录 |
| `POST` | `/api/config/test` | 测试 LLM 配置连通性 |
| `GET` | `/api/data_sources` | 列出数据源 |
| `POST` | `/api/data_sources` | 创建数据源 |
| `GET` | `/api/data_sources/{id}` | 获取数据源详情 |
| `PATCH` | `/api/data_sources/{id}` | 更新数据源 |
| `DELETE` | `/api/data_sources/{id}` | 删除数据源 |
| `POST` | `/api/data_sources/{id}/sync` | 手动同步数据源 |
| `POST` | `/api/data_sources/{id}/pause` | 暂停数据源 |
| `POST` | `/api/data_sources/{id}/resume` | 恢复数据源 |

### 环境变量

根目录 `.env`（**不要提交到 git**）：

| 变量 | 说明 |
|------|------|
| `LLM_API_KEY` / `DASHSCOPE_API_KEY` | LLM 调用密钥 |
| `LLM_BASE_URL` | LLM API Base URL |
| `LLM_MODEL` | 模型名称 |
| `DATABASE_URL` | 默认 `~/.lumen/lumen.db` |
| `DEBUG` | `true`（开发）/ `false`（生产） |
| `EXTERNAL_DATA_ENABLED` | `true` 开启外部数据接入 |
| `EXTERNAL_DATA_DIRS` | 逗号分隔的本地目录路径 |

### Code Style

- Python 3.11+，类型提示（`from __future__ import annotations`）
- SQLAlchemy 2.0 async（`Mapped[...]`, `mapped_column()`）
- Pydantic v2（`BaseSettings`, `BaseModel`）
- 前端 React 19 + TypeScript + Tailwind CSS 4
- ruff 做 lint + format
- pytest + pytest-asyncio（104 条测试）

---

## Explanation: 关键架构决策

### 双管线记忆架构

记忆不是一张表，而是两条独立管线：

**Profile 管线** — 用户的"我是谁"：
- 事件类型：`profile_updated`, `interest_observed`, `value_surfaced`, `preference_learned`, `emotional_pattern`
- 去向：`.md` 投影文件（`memory.md`）
- 召回方式：L0 固定注入 — 每次对话**必定**注入到系统提示词
- 不进搜索索引，因为这些是"应该知道"而非"需要回忆"

**Narrative 管线** — 用户的"我经历了什么"：
- 事件类型：`significant_moment`, `decision_made`, `reflection_added`, `contradiction_noted`, `relationship_noted`
- 去向：FTS5 全文索引 + LanceDB 语义索引
- 召回方式：L2 按需搜索 — 只在用户提问相关时才召回

为什么分两条？
- Profile 是"前置知识"，Agent 每次回复都需要知道用户的性格和偏好
- Narrative 是"背景信息"，只在特定话题时才需要（如用户问"上次我说的那件事"）
- 如果混在一起，搜索噪声会淹没关键画像信息

### L0 / L1 / L2 三层上下文注入

`build_context()` 按优先级分层注入：

**L0 — 固定注入（必含）**
- 用户基础画像（nickname、bio）
- AI 综合画像（about_you.md）
- 对话摘要（conversation.summary）
- 这些不经过搜索，直接读取

**L1 — 精确匹配（去重后注入）**
- 基于 `dedupe_key` 的精确去重事件
- 如用户多次提到"喜欢猫"，只保留一条最新记录
- 写入速度快，查询直接

**L2 — 语义召回（按需搜索）**
- FTS5 关键词搜索 + LanceDB 语义搜索 + 外部文档搜索
- 三路并行（`asyncio.gather`）
- 结果按相关度排序，取 top-N 注入

分层的好处：
- L0 保证"最应该知道的不丢失"
- L1 避免冗余信息淹没上下文窗口
- L2 在有限 token 内召回最相关的背景

### 事件驱动投影

当 `remember()` 写入事件时，同一会话内立即触发投影：

```
用户消息 → Agent 调用 memory_save → 写入 growth_events → 触发投影 →
  Profile 事件 → 更新 memory.md
  Narrative 事件 → 更新 FTS5 / LanceDB
→ commit
```

所有操作在同一数据库事务内：写入和投影要么全成功要么全回滚。如果投影失败（如 LanceDB 不可用），事件不会提交，Agent 会收到错误提示，用户知道"没记住"。

### 工具系统分层

工具不是简单的函数注册，而是四层架构：

1. **ToolDefinition**（元数据）— 名称、描述、输入 schema、权限标签
2. **ToolRegistry**（注册表）— 运行时查找，支持按 toolset 分组
3. **ToolDispatcher**（分发器）— 参数解析、路径解析、安全策略检查
4. **Toolset**（组合）— `default-chat` 等组合，Agent 一次加载一组

为什么分层？
- 定义层和运行层分离：可以静态分析工具清单而不初始化运行时
- 安全策略在分发层统一执行：路径逃逸、循环守卫、预算限制都在这里
- Toolset 支持场景化加载：未来可以针对不同场景加载不同工具组合

### 为什么用 SQLite + FTS5

- **零运维**：单文件，无需部署 PostgreSQL
- **FTS5 原生支持**：全文搜索无需额外服务，CJK 用 trigram tokenizer
- **足够用**：单用户模式，数据量预期 < 100MB
- **事务一致**：事件写入和投影在同一事务，这是 SQLite 的优势

 LanceDB 作为语义搜索的可插拔 Provider，在本地文件系统运行，同样零运维。

---

## Gotchas

- `chat/service.py` 流式对话使用 `db.commit()` 而非 `flush()`，确保用户消息立即落库，流中断不丢失
- `update_profile` 中 `null` 可以清空字段（通过 `model_fields_set` 区分"未传"和"传 null"）
- `chatSession.tsx` 使用 `sessionStorage` 持久化 conversationId，刷新页面不丢失对话
- `modules/agent/pydantic_agent.py` 的 `_tool_runtime` 是全局缓存，配置变更后需重启后端，否则工具列表不会刷新
- `understanding.py` 的 AI 画像生成有 5 分钟防抖（`_DEBOUNCE_SECONDS = 300`），频繁触发不会重复调用 LLM
- `search.py` 三路搜索并行执行，但 Provider 故障会被静默吞掉并返回空列表 — 语义搜索降级为 FTS5，不会报错

---

## Known Limitations

- **无认证**：`user_id` 由客户端 localStorage 控制，无 JWT 鉴权。生产环境需加认证
- **单用户模式**：`demo_user` 硬编码，多用户需改造用户管理
- **Journey 与"此刻"数据重叠**：`emotional_pattern`/`value_surfaced` 同时出现在 Journey 时间线和"此刻"状态，需要分离
- **snapshot.py 耦合 chat 模块**：`build_snapshot` 直接 import `chat.models`，无法独立测试 memory 模块
- **AGENTS.md 仍需人工维护**：文档与代码同步靠人工检查，没有自动化校验

---

## docs/ 索引

设计文档在 `docs/` 下：

- `docs/architecture/` — 系统架构设计与核心决策
- `docs/memory-structure/` — 记忆结构（memory.md + entities/*.md）
- `docs/stories/` — 功能实现 story 记录
- `docs/blog/` — 博客文章
- `docs/issues/` — 问题记录
- `docs/todo/` — Roadmap 与产品模块规划
- `docs/product-brief.md` — 产品简介
- `docs/project-context.md` — AI Agent 编码规则
