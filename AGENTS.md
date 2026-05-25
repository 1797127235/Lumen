# AGENTS.md

## 定位

Lumen — 一个真正认识你的 AI 伙伴。FastAPI + SQLAlchemy + PydanticAI + LiteLLM + SQLite。前端 React 19 + Vite + Tailwind CSS 4。

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

1. **定义输入类型**：在 `lib/tools/` 下的对应模块中使用 `TypedDict` 定义参数 schema
2. **实现 Handler**：在 `lib/tools/` 下的模块中定义 `async def handle_xxx(args: dict, ctx: Any) -> str` 函数
3. **注册工具**：在 `lib/tools/factory.py` 的 `register_all_tools()` 中将工具加入 `all_tools` 列表，自动注册到 `ToolRegistry`
4. **测试**：重启后端，在对话中触发工具调用

关键约束：
- Handler 签名：`async def handle_xxx(args: dict[str, Any], ctx: Any) -> str`
- `ctx` 包含 `db`（AsyncSession）等上下文，Handler 内部可自由读写数据库
- 工具返回必须是字符串（会被 Agent 读取作为工具执行结果）

### 写入长期记忆

Hermes-Pure 架构下没有"事件类型"——所有长期记忆都是 `~/.lumen/memory/{user_id}/memory.md` 里的 Markdown 条目。

1. **主动写入**：Agent 调用 `memory_save` 工具（或 `tellAI` / `update_profile`）→ 追加带日期的 bullet 到 `memory.md` 的 `## Long-term notes` 章节
2. **画像刷新**：写入后台触发 `understanding.py` 从 `memory.md` 重新生成 `about_you.md`（5 分钟防抖）
3. **被动兜底**：`review_service.py` 在对话结束后 fork 一个 Agent 审查本轮，判断是否有值得写入 `memory.md` 的内容
4. **外部召回**：如需语义检索，实现一个 `MemoryProvider` 插件放到 `~/.lumen/plugins/memory/<name>/`，由 `MemoryManager` 在 L2 prefetch 时调用

### 调试流式对话

1. **查看后端日志**：`DEBUG=true` 时日志输出结构化 JSON，包含 `event_kind`、`tool_name`、`conversation_id`
2. **查看 Agent Trace**：`agent_traces` 表记录每一步推理、工具调用、耗时
3. **检查上下文注入**：`memory/build_context()` 的输出被注入到 system prompt 中，日志中搜索 `build_context` 可查看注入内容
4. **强制刷新工具缓存**：修改工具或配置后，`ToolRegistry` 和 `ToolDiscoveryState` 是内存单例，不会自动刷新，需重启后端

### 运行测试

```bash
pytest                    # 全部测试
pytest -x                # 遇到失败立即停
pytest tests/test_memory_manager.py -v   # 单文件详细输出
```

### 添加或管理 Skill

Skill 是可插拔的 Agent 指令包，放在 `lib/skills/builtins/<skill-name>/SKILL.md`。

**当前内置 Skills：**

| Skill | 触发方式 | 说明 |
|-------|---------|------|
| `emotional-partner` | `always: true` | 情感支持与引导，常驻注入 |
| `obsidian-markdown` | `$obsidian-markdown` 或 `skill_load` | Obsidian 风味 Markdown 语法 |
| `obsidian-bases` | `$obsidian-bases` 或 `skill_load` | Obsidian Bases 数据库视图 |
| `json-canvas` | `$json-canvas` 或 `skill_load` | JSON Canvas 白板文件 |
| `obsidian-cli` | `$obsidian-cli` 或 `skill_load` | Obsidian CLI 命令交互 |
| `defuddle` | `$defuddle` 或 `skill_load` | 网页内容提取（Defuddle） |

**添加新 Skill：**

1. 在 `lib/skills/builtins/` 下创建 `<skill-name>/SKILL.md`
2. 顶部写 YAML frontmatter：
   ```yaml
   ---
   name: skill-name
   description: 一句话说明用途，Agent 用此判断何时加载
   metadata:
     always: false          # 是否常驻注入
     requires:
       env: ["ENV_KEY"]     # 可选：依赖的环境变量
   ---
   ```
3. 正文用 Markdown 写指令内容
4. 重启后端生效

**Frontmatter 格式兼容**：支持 Lumen 原生 `metadata:` 包裹格式，也支持 agentskills.io 规范的顶层字段格式。

**Agent 使用方式**：
- 用户在消息中输入 `$skill-name`，系统自动检测并注入该 Skill 正文
- Agent 也可主动调用 `skill_load(skill_name="...")` 加载

---

## Reference

### 项目结构

```
Lumen/
├── main.py                     # FastAPI 入口，lifespan 中自动建表 + 初始化
├── core/                       # 基础设施
│   ├── config.py               # pydantic-settings，从根目录 .env + ~/.lumen/config.json 加载
│   ├── db.py                   # SQLAlchemy AsyncEngine + Base + get_async_session_maker
│   ├── migrations.py           # SQLite 兼容迁移、FTS5 表、触发器
│   ├── startup.py              # 启动初始化（建表、Channel 启动、关闭清理）
│   └── agent.py                # PydanticAI Agent 定义 + 动态系统提示词
├── shared/                     # 跨模块通用工具
│   ├── errors.py               # 统一错误体系（LumenError + severity/category/retryable）
│   ├── logging.py              # structlog 日志：控制台彩色 + 文件纯文本，上下文绑定
│   ├── llm_usage.py            # LLM 调用用量追踪
│   └── path_utils.py           # 路径工具（find_project_root）
├── lib/                        # 业务模块（按领域拆分）
│   ├── model_registry.py       # SQLAlchemy 模型注册
│   ├── bus/                    # 事件总线 + 消息队列
│   │   ├── event_bus.py        # EventBus：进程内事件发布订阅
│   │   └── queue.py            # MessageBus：异步消息队列（inbound/outbound）
│   ├── channels/               # 多渠道抽象（Web / Telegram / CLI）
│   │   ├── base.py             # BaseChannel 抽象基类
│   │   ├── web.py              # WebChannel：SSE 流式对话
│   │   ├── telegram.py         # Telegram Bot 渠道
│   │   └── cli.py              # CLI 渠道
│   ├── chat/                   # 对话模块
│   │   ├── agent_runner.py     # AgentRunner：后台消费消息，运行 Agent Loop
│   │   ├── session.py          # 会话状态管理
│   │   ├── persistence.py      # 消息持久化
│   │   ├── session_files.py    # 会话附件管理
│   │   ├── lock.py             # 对话并发锁
│   │   ├── summary.py          # 对话摘要后台任务
│   │   ├── agent_trace.py      # AgentTrace 可观测性
│   │   └── event_handlers.py   # Agent 事件处理（流式输出、工具调用跟踪）
│   ├── memory/                 # 记忆层（Hermes-Pure 文件优先：memory.md 唯一真相源）
│   │   ├── provider.py         # MemoryProvider 抽象接口 + NoOpMemoryProvider
│   │   ├── manager.py          # MemoryManager 进程级单例（内置文件记忆 + 外部 provider 编排）
│   │   ├── builtin_provider.py # BuiltinMemoryProvider（文件 backed，L0 冻结快照）
│   │   ├── loader.py           # 从 ~/.lumen/plugins/memory/ 发现 provider 插件
│   │   ├── markdown.py         # AsyncMarkdownStore：memory.md / about_you.md 原子读写
│   │   ├── context_fence.py    # <memory-context> 围栏构建 + 注入内容清洗
│   │   ├── snapshot.py         # 薄兼容层，委托 MemoryManager 构建 L0 + L1 近期对话
│   │   ├── understanding.py    # AI 综合画像生成（memory.md → about_you.md，5 分钟防抖）
│   │   └── review_service.py   # 后台记忆审查（Agent fork 审查对话，写入 memory.md）
│   ├── profile/                # 画像模块
│   │   ├── models.py           # User + UserProfile（通用伙伴画像）
│   │   └── schemas.py          # ProfilePayload, KeyValuePayload, DecisionPayload
│   ├── partner/              # 伙伴系统（情绪、主动对话、潜意识）
│   │   ├── models.py           # LumenState + LumenPresence + LumenThought ORM
│   │   ├── mood_inference.py   # 情绪推断逻辑
│   │   └── presence.py         # 在线状态与主动触发管理
│   ├── config/                 # 配置模块
│   │   └── service.py          # 配置业务逻辑
│   ├── providers/              # LLM Provider 目录
│   │   ├── __init__.py         # PROVIDER_CATALOG + ProviderRegistry
│   │   ├── _client.py          # probe_provider / build_auth_headers
│   │   └── _validation.py      # filter_discovered_models
│   └── tools/                  # Agent 工具系统
│       ├── __init__.py
│       ├── _base.py            # ToolDef dataclass + tool_ok / tool_error
│       ├── _registry.py        # ToolRegistry：全局工具注册表 + 搜索
│       ├── _discovery.py       # ToolDiscoveryState：conversation 级工具可见性缓存
│       ├── _middleware.py      # 工具中间件
│       ├── _search_tool.py     # tool_search：关键词搜索可用工具
│       ├── factory.py          # 工具工厂（注册所有内置工具 + MCP 工具）
│       ├── skill_load.py       # skill_load 工具（动态加载 Skill）
│       ├── memory.py           # memory_search, memory_save
│       ├── profile.py          # get_profile, update_profile
│       ├── shell.py            # Shell 命令执行工具
│       ├── files.py            # 文件读写工具
│       ├── web_search.py       # 网络搜索（需配置 SEARCH_PROVIDER）
│       └── mcp/                # MCP 工具桥接
│           ├── client_manager.py
│           ├── config_store.py
│           ├── models.py
│           ├── tool_bridge.py
│           └── transport.py
├── server/                     # API 路由层
│   └── routes/
│       ├── chat.py             # POST /api/chat (SSE), GET /api/chat/history
│       ├── memory.py           # 记忆管理 API
│       ├── config.py           # 配置 API
│       ├── health.py           # GET /api/health
│       ├── providers.py        # Provider 管理 API
│       ├── mcp.py              # MCP 服务器管理 API
│       └── partner.py        # 伙伴系统 API（情绪状态）
├── src/                        # React 前端（Vite）
│   ├── App.tsx
│   ├── main.tsx                # 路由配置
│   ├── index.css
│   ├── pages/
│   │   ├── Chat.tsx            # SSE 流式对话 + 历史抽屉 + 思考过程
│   │   ├── Profile.tsx         # AI 综合画像 + 主动塑造 + 模式/心愿/此刻/时间线
│   │   ├── Memories.tsx        # 记忆列表管理
│   │   ├── InnerWorld.tsx      # 伙伴内心状态（建设中）
│   │   └── Settings.tsx        # Provider 选择 + API Key
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
│           └── partner.ts
├── src-tauri/                  # Tauri v2 桌面壳（Rust）
│   └── src/
│       ├── lib.rs              # start_backend / stop_backend
│       └── main.rs
├── tests/                      # pytest 测试
├── docs/                       # 设计文档
│   ├── architecture/           # 系统架构设计
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
| `GET` | `/api/health` | 健康检查 |
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
| `GET` | `/api/providers/summary` | Provider 汇总信息 |
| `POST` | `/api/providers/fetch-models` | 抓取 Provider 模型列表 |
| `GET` | `/api/providers/{name}/discovered-models` | 获取已发现模型 |
| `POST` | `/api/providers/test` | 测试 Provider 连通性 |
| `PUT` | `/api/providers/{name}/models/{model_id}` | 更新 Provider 模型配置 |
| `GET` | `/api/mcp/servers` | MCP 服务器列表 |
| `POST` | `/api/mcp/servers` | 添加 MCP 服务器 |
| `GET` | `/api/mcp/servers/{name}` | 获取 MCP 服务器详情 |
| `PUT` | `/api/mcp/servers/{name}` | 更新 MCP 服务器 |
| `DELETE` | `/api/mcp/servers/{name}` | 删除 MCP 服务器 |
| `POST` | `/api/mcp/servers/{name}/test` | 测试 MCP 服务器连通性 |
| `POST` | `/api/mcp/servers/{name}/refresh` | 刷新 MCP 工具列表 |
| `GET` | `/api/mcp/tools` | 列出所有 MCP 工具 |
| `GET` | `/api/memory/observations` | 观察事件列表 |
| `PATCH` | `/api/memory/{event_id}` | 更新指定记忆事件 |
| `POST` | `/api/memory/{event_id}/review` | 审查指定记忆事件 |
| `GET` | `/api/partner/mood` | 获取 Lumen 当前情绪状态 |

### 环境变量

根目录 `.env`（**不要提交到 git**）：

| 变量 | 说明 |
|------|------|
| `LLM_API_KEY` / `DASHSCOPE_API_KEY` | LLM 调用密钥 |
| `LLM_BASE_URL` | LLM API Base URL |
| `LLM_MODEL` | 模型名称 |
| `DATABASE_URL` | 默认 `~/.lumen/lumen.db` |
| `DEBUG` | `true`（开发）/ `false`（生产） |
| `SEARCH_PROVIDER` | 搜索 Provider（tavily / serper / brave）|
| `SEARCH_API_KEY` | 搜索 API 密钥 |

### Code Style

- Python 3.11+，类型提示（`from __future__ import annotations`）
- SQLAlchemy 2.0 async（`Mapped[...]`, `mapped_column()`）
- Pydantic v2（`BaseSettings`, `BaseModel`）
- structlog + stdlib 混合日志，`shared/logging.py` 统一配置
- 前端 React 19 + TypeScript + Tailwind CSS 4
- ruff 做 lint + format
- pytest + pytest-asyncio

### 错误处理体系

`shared/errors.py` 定义统一错误体系，对标生产级错误处理：

- **`LumenError`** 基类：含 `code`, `severity`, `category`, `retryable`, `http_status`, `trace_id`
- **`wrap()`**：包装第三方异常，保留原始堆栈
- **快捷函数**：`not_found()`, `bad_request()`, `forbidden()`, `config_missing_key()`
- **HTTP 转换**：`to_http_exception()` 自动映射状态码
- **全局 FastAPI handler**：`handle_lumen_error`, `handle_fallback_error`

所有业务层异常应继承 `LumenError`，避免直接抛裸 `Exception`。

### Provider 目录

`lib/providers/` 管理 LLM Provider 的发现、验证和配置：

- **`PROVIDER_CATALOG`**：6 个内置 provider（openai / dashscope / anthropic / google / ollama / siliconflow），含 chat_models / embedding_models / base_url / auth_type
- **`ProviderRegistry`**：管理 `~/.lumen/providers.json` 中的用户自定义 provider 和凭证
- **`probe_provider()`**：连通性探测，验证 API key 和 base_url
- **`filter_discovered_models()`**：过滤和校验模型列表

关键约束：`lib/providers/__init__.py` 不导入 `core.config`，`USER_DATA_DIR` 直接内联，避免循环导入。

### 日志系统

`shared/logging.py` 使用 structlog + stdlib 混合架构：

- **控制台输出**：彩色结构化日志，`ConsoleRenderer(colors=True)`
- **文件输出**：`logs/lumen.log` 纯文本（无 ANSI），`plain_traceback`，10MB 轮转
- **上下文绑定**：`bind_chat_context(conversation_id, user_id)` / `unbind_chat_context()` — Agent Loop 内自动附加上下文到所有子日志
- **请求中间件**：`RequestLoggingMiddleware` 自动绑定 `request_id`/`path`/`method`
- **噪音过滤**：SQLAlchemy / aiosqlite 日志级别设为 WARNING，彻底消除调试噪音

查看实时日志：`Get-Content logs/lumen.log -Wait -Tail 20`

---

## Explanation: 关键架构决策

### 多渠道 Bus/Channel 架构

Lumen 通过统一的 Bus/Channel 层同时支持三种接入方式，后端业务逻辑与渠道完全解耦：

```
WebChannel (SSE)        TelegramChannel (Polling)     CLIChannel (stdin)
      │                          │                           │
      └──────────────────────────┼───────────────────────────┘
                                 ↓
                         MessageBus (lib/bus/)
                    InboundMessage → AgentRunner
                    AgentRunner → OutboundMessage
                                 ↓
                         EventBus (进程内 pub/sub)
              StreamDeltaReady / ToolCallStarted / TurnStarted / ...
```

- **BaseChannel**（`lib/channels/base.py`）：抽象基类，`start()`/`stop()`/`send_message()`
- **WebChannel**（`lib/channels/web.py`）：SSE 流式，前端 / Tauri 接入
- **TelegramChannel**（`lib/channels/telegram.py`）：Polling 模式，缓冲流式增量，整条发送
- **CLIChannel**（`lib/channels/cli.py`）：读 stdin，写 stdout
- **AgentRunner**（`lib/chat/agent_runner.py`）：后台消费 MessageBus，驱动 PydanticAI Agent Loop

**Tauri 打包**（`src-tauri/src/lib.rs`）：将 `python -m uvicorn main:app` 作为 sidecar subprocess 启动，通过 Windows Job Object 绑定生命周期，关闭窗口自动终止 Python 进程。Tauri 是分发壳，不影响 Channel 运行模式。

### 文件优先记忆架构（Hermes-Pure）

2026-05-25 重构：移除 GrowthEvent 事件管线，改为 Markdown 文件作为长期记忆的唯一真相源。运行时不再有记忆表、记忆 FTS、事件投影缓存或事件审核状态。

每个用户对应 `~/.lumen/memory/{user_id}/`：
- `memory.md`：可编辑的长期记忆文档（唯一真相源）
- `about_you.md`：由 `memory.md` 生成的 AI 用户画像

写入路径：`memory_save` / `tellAI` / `update_profile` 直接写 `memory.md` → 后台刷新 `about_you.md`（见上文「写入长期记忆」）。

**作用域**：文件按 `user_id`（跨渠道共享一份记忆）；外部召回与会话生命周期按 `session_key`（`channel` + `chat_id`），避免 web 与 telegram 串台。

### L0 / L1 / L2 三层上下文

**L0 — 冻结快照（注入 system prompt）**
- `about_you.md`（缺失时降级到 `memory.md`），由 `MemoryManager.build_system_prompt()` 经 `BuiltinMemoryProvider` 读取（`snapshot.py` 是薄兼容层）
- 按 conversation（`chat_id`）冻结：进入对话取一次快照，对话内多轮复用同一份 system prompt（命中 prefix cache），5 分钟 LRU 缓存
- mid-conversation 的 `memory_save` 写盘但不改当前对话的 system prompt，下一段对话才生效

**L1 — 近期对话上下文**
- 最近 5 个对话、7 天内，每个对话最多 3 条消息（`snapshot.py` 的 `_fetch_recent_conversations`）
- 数据来源是 `Conversation + Message` 表，通过 `set_conversation_fetcher()` 依赖注入，解耦 memory 与 chat 模块
- 本就在消息历史里，无需额外重复注入

**L2 — 外部召回（按需，`<memory-context>` 围栏）**
- `MemoryProvider.prefetch(query)`，按 `session_key` 隔离
- 无外部 provider 时为空——内置文件记忆的内容已在 L0 冻结快照里

### 轮次同步与压缩

- 一次成功 assistant turn 后调用 `MemoryManager.sync_all(...)`，让外部 provider 摄取对话轮次；内置文件记忆不自动保存每轮，依赖有意图的 `memory_save` / `tellAI` / 后台 review
- 压缩前调用 `MemoryManager.on_pre_compress(messages)`，将 provider 返回文本追加到压缩 prompt

### 工具系统分层

工具系统采用注册表 + 动态发现模式：

1. **ToolDef**（元数据）— `lib/tools/_base.py` 中定义，含名称、描述、输入 schema、执行函数、风险等级、标签等
2. **注册表层** — `lib/tools/_registry.py` 的 `ToolRegistry` 管理全量工具索引，提供搜索和按名称过滤 schema 能力
3. **发现层** — `lib/tools/_discovery.py` 的 `ToolDiscoveryState` 按 `conversation_id` 维护预加载缓存（LRU），实现 deferred 工具的按需解锁
4. **工厂层** — `lib/tools/factory.py` 负责组装所有工具实例并注册到 `ToolRegistry`
5. **中间件层** — `lib/tools/_middleware.py` 提供通用横切能力
6. **具体工具** — `lib/tools/memory.py`, `profile.py`, `shell.py`, `files.py`, `web_search.py` 等
7. **MCP 桥接** — `lib/tools/mcp/` 将外部 MCP 服务器暴露为 Agent 工具

为什么这样设计？
- **动态发现**：非核心工具默认隐藏，Agent 需要通过 `tool_search` 解锁，减少首次调用的 schema 体积
- **扁平化注册**：新增工具只需在 `lib/tools/` 下新增模块并在 `factory.py` 注册
- **MCP 桥接**：任何兼容 MCP 的服务器都可被 Agent 调用，外部工具生态零成本接入
- **中间件层**：统一处理横切关注点（日志、重试、超时），业务工具只关注核心逻辑

### 为什么用 SQLite + FTS5

- **零运维**：单文件，无需部署 PostgreSQL
- **FTS5 原生支持**：外部文档（`external_items`）全文搜索无需额外服务，CJK 用 trigram tokenizer
- **足够用**：单用户模式，数据量预期 < 100MB

长期记忆本身不再走 SQLite，而是文件优先（`memory.md`）。SQLite 现承载对话/消息、外部文档索引、伙伴系统状态等。外部语义召回由 `MemoryProvider` 插件提供。

---

## Gotchas

- `lib/chat/agent_runner.py` 流式对话在持久化时使用 `db.commit()`，确保用户消息立即落库，流中断不丢失
- `update_profile` 中 `null` 可以清空字段（通过 `model_fields_set` 区分"未传"和"传 null"）
- `chatSession.tsx` 使用 `sessionStorage` 持久化 conversationId，刷新页面不丢失对话
- `lib/tools/_registry.py` 的 `ToolRegistry` 和 `lib/tools/_discovery.py` 的 `ToolDiscoveryState` 是内存单例，配置变更后需重启后端，否则工具列表和发现状态不会刷新
- `understanding.py` 的 AI 画像生成有 5 分钟防抖（`_DEBOUNCE_SECONDS = 300`），频繁触发不会重复调用 LLM
- 外部 `MemoryProvider` 故障被静默隔离（`MemoryManager` 记录 warning 并降级到内置文件记忆），不会打断聊天
- `shared/logging.py` 使用 structlog + stdlib 混合模式，`bind_chat_context()` 在 Agent Loop 中自动绑定 `conversation_id`/`user_id` 到所有子日志
- FTS5 触发器更新时必须用 `DELETE FROM external_items_fts WHERE rowid = old.rowid`，不能对虚拟表使用 `INSERT INTO ... VALUES(..., 'delete', ...)` 特殊语法
- `lib/providers/` 与 `core.config` 解耦避免循环导入：`USER_DATA_DIR` 直接内联为 `Path.home() / ".lumen"`，不通过 `core.config` 获取

---

## Known Limitations

- **无认证**：`user_id` 由客户端 localStorage 控制，无 JWT 鉴权。生产环境需加认证
- **单用户模式**：`demo_user` 硬编码，多用户需改造用户管理
- **Journey 与"此刻"数据重叠**：`emotional_pattern`/`value_surfaced` 同时出现在 Journey 时间线和"此刻"状态，需要分离
- **snapshot.py 耦合 chat 模块**：`build_snapshot` 直接 import `chat.models`，无法独立测试 memory 模块
- **数据层耦合**：`lib/providers/__init__.py` 直接操作文件系统（`~/.lumen/providers.json`），缺乏抽象层
- **搜索 Provider 未配置时 web_search 工具静默失败**：缺少运行时的用户友好提示
- **ToolRegistry 是内存单例**：多 worker 部署（如 gunicorn）时每个进程有独立的工具缓存和发现状态

---

## docs/ 索引

设计文档在 `docs/` 下：

- `docs/architecture/` — 系统架构设计与核心决策
- `docs/stories/` — 功能实现 story 记录
- `docs/blog/` — 博客文章
- `docs/issues/` — 问题记录
- `docs/todo/` — Roadmap 与产品模块规划
- `docs/product-brief.md` — 产品简介
- `docs/project-context.md` — AI Agent 编码规则
