# AGENTS.md

## What This Is

Lumen — 一个真正认识你的 AI 伴侣。FastAPI + SQLAlchemy + PydanticAI + LiteLLM + SQLite。前端 React 19 + Vite + Tailwind CSS 4。

## How to Run

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装前端依赖
npm install

# 方式一：Web 本地开发
# Windows: 双击 start.bat
# PowerShell: .\start.ps1
# 打开 http://localhost:5173

# 方式二：桌面应用（Tauri，推荐）
cargo tauri dev
# 一行命令，全部自动拉起。Python 后端作为 sidecar 子进程由 Rust 管理生命周期。
```

启动后自动建表（SQLite），无需手动迁移。首次启动自动初始化记忆目录（`~/.lumen/memory/`）。

## Project Structure

```
career-os/
├── backend/                    # FastAPI 后端
│   ├── main.py                 # FastAPI 入口，lifespan 中自动建表 + 初始化
│   ├── config.py               # pydantic-settings，从根目录 .env 加载
│   ├── db.py                   # SQLAlchemy AsyncEngine + Base + get_async_session_maker
│   ├── db_migrations.py        # SQLite 兼容迁移、FTS5 表、触发器
│   ├── logging_config.py       # 结构化日志配置
│   ├── agent/
│   │   ├── pydantic_agent.py   # PydanticAI Agent 定义 + 动态系统提示词
│   │   ├── event_handlers.py   # Agent 事件处理（流式输出、工具调用跟踪）
│   │   ├── deps.py             # Agent 依赖注入（LumenDeps）
│   │   └── tools/
│   │       ├── file_security.py          # 文件安全（路径校验、大小限制、二进制检测）
│   │       ├── builtin/                  # 内置工具 Handler
│   │       │   ├── memory.py             # memory_search, memory_save
│   │       │   ├── profile.py            # get_profile, update_profile
│   │       │   ├── files.py              # 文件读写工具
│   │       │   └── external.py           # search_external_docs（外部数据搜索）
│   │       ├── core/                     # 工具运行时核心
│   │       │   ├── registry.py           # ToolRegistry（工具注册表）
│   │       │   ├── dispatcher.py         # ToolDispatcher（调用分发）
│   │       │   ├── definitions.py        # ToolDefinition（工具元数据）
│   │       │   ├── factory.py            # ToolRuntime 工厂 + Toolset 注册
│   │       │   ├── context.py            # ToolRuntimeContext（运行时上下文）
│   │       │   ├── policies.py           # 安全策略（路径、预算、循环守卫、审批）
│   │       │   └── toolsets.py           # Toolset 定义与解析
│   │       ├── adapters/
│   │       │   └── pydanticai.py         # PydanticAI 工具适配器
│   │       └── mcp/
│   │           └── __init__.py           # MCP 预留
│   ├── api/
│   │   ├── chat/
│   │   │   ├── routes.py       # POST /api/chat (SSE), GET /api/chat/history, ...
│   │   │   └── lock.py         # 对话并发锁
│   │   └── routers/
│   │       ├── health.py       # GET /api/health
│   │       ├── memory.py       # 记忆管理路由（stats/list/reset/rebuild/search/...）
│   │       ├── config.py       # GET/POST /api/config, /api/config/providers, /api/config/test
│   │       └── summary.py      # 对话摘要后台任务（无路由，被 chat_service 调用）
│   ├── application/
│   │   ├── chat_service.py     # 对话业务：Agent Loop 集成 + SSE 流式输出
│   │   ├── chat_session.py     # 会话状态管理
│   │   ├── chat_persistence.py # 消息持久化
│   │   ├── profile_service.py  # 画像业务
│   │   ├── resume_service.py   # 简历解析
│   │   └── review_service.py   # 后台记忆审查兜底
│   ├── domain/
│   │   ├── models/
│   │   │   ├── user.py         # User + UserProfile（含 profile_data JSON）
│   │   │   ├── conversation.py # Conversation
│   │   │   ├── message.py      # Message
│   │   │   ├── growth_event.py # GrowthEvent 事件溯源
│   │   │   └── agent_trace.py  # AgentTrace 可观测性
│   │   └── schemas/
│   │       ├── profile.py      # ProfileResponse, ProfileUpdate, SkillItem（含 context）
│   │       └── memory.py       # EventType, ENTITY_TYPE_MAP, EVENT_PAYLOAD_MAP
│   ├── ingestion/              # 外部数据接入（Phase 2a+）
│   │   ├── connector.py        # DataSourceConnector ABC + RawDocument
│   │   ├── pipeline.py         # IngestionPipeline（扫描 → 索引状态机）
│   │   ├── store.py            # IngestionStore（JSON 原子写入，dedup 状态）
│   │   ├── retry.py            # jittered_retry 装饰器
│   │   └── connectors/
│   │       └── filesystem.py   # FilesystemConnector（.md/.txt 扫描 + watchdog）
│   ├── memory/                 # 记忆层（双管线：Profile + Narrative）
│   │   ├── facade.py           # LumenMemory 统一门面
│   │   ├── search.py           # FTS5 全文搜索（growth_events_fts + external_items_fts）
│   │   ├── searcher.py         # recall() 统一召回入口
│   │   ├── projection.py       # growth_events → .md 投影
│   │   ├── markdown.py         # .md 文件读写
│   │   ├── writer.py           # 事件写入
│   │   ├── relational_store.py # SQLite 关系存储
│   │   ├── cognify_loop.py     # Cognee 后台 cognify 任务
│   │   ├── classifier.py       # 事件分类
│   │   ├── understanding.py    # AI 综合画像生成
│   │   ├── snapshot.py         # 记忆快照
│   │   ├── events_merger.py    # 事件合并
│   │   ├── datasets.py         # Cognee dataset 管理
│   │   ├── projection.py       # growth_events → .md 投影
│   │   ├── markdown.py         # .md 文件读写
│   │   ├── writer.py           # 事件写入
│   │   ├── relational_store.py # SQLite 关系存储
│   │   ├── search.py           # 全文搜索（FTS5 + Provider）
│   │   └── searcher.py         # recall() 统一召回入口
│   └── utils/
│       ├── date_utils.py       # 日期工具
│       ├── json_utils.py       # JSON 工具
│       ├── parsers.py          # 解析工具
│       └── path_utils.py       # 路径工具
├── src/                        # React 前端（Vite）
│   ├── App.tsx
│   ├── main.tsx
│   ├── index.css
│   ├── pages/
│   │   ├── Chat.tsx            # SSE 流式对话 + 历史抽屉 + 空态示例
│   │   ├── Profile.tsx         # 画像页：教育详情/技能/获奖 + inline 编辑
│   │   ├── Memories.tsx        # 记忆管理页
│   │   └── Settings.tsx        # 设置页：Provider 选择 + API Key
│   ├── components/
│   │   ├── Card.tsx
│   │   ├── EmptyState.tsx
│   │   ├── ProfileActions.tsx
│   │   └── Sidebar.tsx
│   └── lib/
│       ├── api.ts              # 后端 API 调用 + SSE 解析
│       ├── chatSession.tsx     # 全局聊天状态管理（Context Provider）
│       ├── userId.ts           # 用户 ID 管理
│       ├── thinkSegments.ts    # 思考片段处理
│       └── api/                # API 模块拆分
│           ├── core.ts
│           ├── chat.ts
│           ├── memory.ts
│           └── config.ts
├── src-tauri/                  # Tauri v2 桌面壳（Rust）
│   └── src/
│       ├── lib.rs              # start_backend / stop_backend + Tauri commands
│       └── main.rs
├── tests/                      # pytest 测试用例
├── docs/                       # 设计文档
│   ├── architecture/           # 系统架构设计与核心决策
│   ├── memory-structure/       # 记忆结构（memory.md + entities/*.md）
│   ├── stories/                # 功能实现 story 记录
│   ├── blog/                   # 博客文章
│   └── issues/                 # 问题记录
├── .github/workflows/          # CI/CD（lint + test + build）
├── pyproject.toml              # ruff + pytest 配置
├── start.ps1 / start.bat       # Web 本地开发启动脚本
└── requirements.txt            # Python 依赖
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | 健康检查（含 Cognee 状态） |
| `POST` | `/api/chat` | SSE 流式对话（Agent Loop），body: `{ message, conversation_id?, user_id? }` |
| `GET` | `/api/chat/history?user_id=&limit=` | 对话历史列表 |
| `GET` | `/api/chat/{conversation_id}` | 单条会话消息详情 |
| `DELETE` | `/api/chat/{conversation_id}` | 删除对话及其消息 |
| `GET` | `/api/memory/me` | 读取用户画像记忆内容 |
| `GET` | `/api/memory/stats?user_id=` | 记忆统计（状态、事件数） |
| `GET` | `/api/memory/list?user_id=` | 记忆列表 |
| `POST` | `/api/memory/reset?user_id=` | 清空用户记忆 |
| `POST` | `/api/memory/rebuild` | 重建记忆（.md + Cognee） |
| `GET` | `/api/memory/search` | 语义搜索记忆 |
| `DELETE` | `/api/memory/{event_id}` | 删除指定记忆事件 |
| `POST` | `/api/memory/upload-resume` | 上传简历解析并生成事件 |
| `GET` | `/api/memory/profile-structured` | 获取结构化画像数据 |
| `POST` | `/api/memory/profile-update` | 前端手动更新画像 |
| `GET` | `/api/memory/understanding` | 获取 AI 综合画像 |
| `POST` | `/api/memory/understanding/refresh` | 手动触发 AI 画像重新生成 |
| `POST` | `/api/memory/understanding/correct` | 用户手动纠正 AI 画像文本 |
| `GET` | `/api/config` | 获取当前用户配置（含脱敏 key） |
| `POST` | `/api/config` | 更新用户配置 |
| `GET` | `/api/config/providers` | Provider 目录（前端动态拉取） |
| `POST` | `/api/config/test` | 测试 LLM 配置连通性 |

## Key Architecture Decisions

- **Agent 系统**：PydanticAI 实现（`pydantic_agent.py`），支持工具调用、动态系统提示词、流式输出
- **工具系统**：分层架构 — `ToolDefinition`（元数据）→ `ToolRegistry`（注册表）→ `ToolDispatcher`（分发）→ `Toolset`（组合），内置工具在 `backend/agent/tools/builtin/`，安全策略在 `backend/agent/tools/core/policies.py`
- **LLM 路由**：`llm_router.py` 支持多 Provider（DashScope/OpenAI/DeepSeek 等），通过 LiteLLM 统一调用
- **记忆层**：双管线 — Profile 事件（→ `.md` 投影）+ Narrative 事件（→ FTS5 `growth_events_fts`）；Cognee 提供语义搜索；`external_items` 表（Phase 2a）存储外部文档索引
- **数据库**：SQLite（`lumen.db`），`db_migrations.py` 集中管理所有 DDL（含 FTS5 虚拟表与触发器），`lifespan` 中调用 `migrate_sqlite()`
- **画像数据模型**：扩展字段存入 `profile_data` JSON 列，零 ORM 列新增
- **聊天状态**：`chatSession.tsx` 全局 Context Provider，跨页面保持对话状态
- **可观测性**：`agent_traces` 表记录 Agent 推理步骤、工具调用、耗时
- **外部数据接入**：`DataSourceConnector` 抽象基类隔离变化，`IngestionPipeline` 驱动扫描 → dedup → 写入 `external_items`，FTS5 由 SQLite trigger 同步

## .env

根目录有 `.env` 文件，包含 LLM API Key 等。**不要提交到 git**。

关键配置：
- `LLM_API_KEY` / `DASHSCOPE_API_KEY` — LLM 调用
- `DATABASE_URL` — 默认 `~/.lumen/lumen.db`
- `DEBUG` — `true`（开发）/ `false`（生产）
- `EXTERNAL_DATA_ENABLED` — `true` 开启外部数据接入（Phase 2a）
- `EXTERNAL_DATA_DIRS` — 逗号分隔的本地目录路径，如 `C:\Obsidian,C:\Notes`

## Code Style

- Python 3.11+，类型提示（`from __future__ import annotations`）
- SQLAlchemy 2.0 async（`Mapped[...]`, `mapped_column()`）
- Pydantic v2（`BaseSettings`, `BaseModel`）
- 前端 React 19 + TypeScript + Tailwind CSS 4
- ruff 做 lint + format（`pyproject.toml` 配置）
- pytest + pytest-asyncio（30 条测试，`pytest` 运行）

## Gotchas

- `chat_service.py` 流式对话使用 `db.commit()` 而非 `flush()`，确保用户消息立即落库，流中断不丢失
- `update_profile` 中 `null` 可以清空字段（通过 `model_fields_set` 区分"未传"和"传 null"）
- `chatSession.tsx` 使用 `sessionStorage` 持久化 conversationId，刷新页面不丢失对话
- `backend/agent/pydantic_agent.py` 的 `_tool_runtime` 是全局缓存，配置变更后需清空缓存，否则工具列表不会刷新

## Known Limitations

- **无认证**：`user_id` 由客户端 localStorage 控制，无 JWT 鉴权。生产环境需加认证
- **单用户模式**：demo_user 硬编码，多用户需改造

## docs/

设计文档在 `docs/` 下：
- `docs/architecture/` — 系统架构设计与核心决策
- `docs/memory-structure/` — 记忆结构（memory.md + entities/*.md）
- `docs/stories/` — 功能实现 story 记录
- `docs/blog/` — 博客文章
- `docs/issues/` — 问题记录
- `docs/product-brief.md` — 产品简介
- `docs/project-context.md` — AI Agent 编码规则
