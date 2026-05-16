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
│   ├── model_registry.py       # SQLAlchemy 模型注册（供 alembic/migration 使用）
│   ├── core/                   # 基础设施
│   │   ├── config.py           # pydantic-settings，从根目录 .env 加载
│   │   ├── db.py               # SQLAlchemy AsyncEngine + Base + get_async_session_maker
│   │   ├── logging.py          # 结构化日志配置
│   │   ├── migrations.py       # SQLite 兼容迁移、FTS5 表、触发器
│   │   └── startup.py          # 启动初始化（建表、IngestionPipeline、DocumentIndexProvider）
│   ├── shared/                 # 跨模块通用工具
│   │   ├── date_utils.py       # 日期工具
│   │   ├── json_utils.py       # JSON 工具
│   │   ├── parsers.py          # 解析工具
│   │   └── path_utils.py       # 路径工具
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
│       │       │   ├── external.py      # search_external_docs（外部数据搜索）
│       │       │   └── schemas.py       # 工具输入/输出 Pydantic schema
│       │       ├── core/                # 工具运行时核心
│       │       │   ├── registry.py      # ToolRegistry（工具注册表）
│       │       │   ├── dispatcher.py    # ToolDispatcher（调用分发）
│       │       │   ├── definitions.py   # ToolDefinition（工具元数据）
│       │       │   ├── factory.py       # ToolRuntime 工厂 + Toolset 注册
│       │       │   ├── context.py       # ToolRuntimeContext（运行时上下文）
│       │       │   ├── policies.py      # 安全策略（路径、预算、循环守卫、审批）
│       │       │   └── toolsets.py      # Toolset 定义与解析
│       │       ├── adapters/
│       │       │   └── pydanticai.py    # PydanticAI 工具适配器
│       │       └── mcp/                 # MCP 预留
│       ├── chat/               # 对话模块
│       │   ├── router.py       # POST /api/chat (SSE), GET /api/chat/history, ...
│       │   ├── service.py      # 对话业务：Agent Loop 集成 + SSE 流式输出
│       │   ├── session.py      # 会话状态管理
│       │   ├── persistence.py  # 消息持久化
│       │   ├── lock.py         # 对话并发锁
│       │   ├── summary.py      # 对话摘要后台任务
│       │   └── models.py       # Conversation + Message
│       ├── memory/             # 记忆层（双管线：Profile + Narrative）
│       │   ├── facade.py       # LumenMemory 统一门面（多继承组合）
│       │   ├── models.py       # GrowthEvent ORM 模型
│       │   ├── classifier.py   # 事件分类（Profile / Narrative 路由）
│       │   ├── writer.py       # 事件写入（单条/批量，含去重）
│       │   ├── searcher.py     # 搜索/召回 + 上下文构建（L0/L1/L2 分层注入）
│       │   ├── search.py       # 全文搜索（FTS5 + Provider）+ 外部文档搜索
│       │   ├── relational_store.py  # Repository 模式 + FTS5 触发器管理
│       │   ├── projection.py   # .md 投影同步、全量重建、删除、重置
│       │   ├── markdown.py     # .md 文件原子读写 + growth_events → memory.md 投影
│       │   ├── snapshot.py     # Agent 系统提示词分层快照（L0/L1/L2）
│       │   ├── events_merger.py  # 事件合并与 memory.md 生成（纯函数层）
│       │   ├── understanding.py  # AI 综合画像生成（about_you.md，LLM 驱动）
│       │   ├── router.py         # 记忆管理 API 路由（16 个端点）
│       │   └── review_service.py # 后台记忆审查（Agent fork 审查对话）
│       ├── profile/            # 画像模块
│       │   ├── models.py       # User + UserProfile（含 profile_data JSON）
│       │   ├── schemas.py      # ProfileResponse, ProfileUpdate, SkillPayload 等
│       │   ├── service.py      # 画像结构化读写业务
│       │   └── resume_service.py  # 简历解析
│       ├── config/             # 配置模块
│       │   ├── router.py       # GET/POST /api/config, /api/config/providers, /api/config/test
│       │   └── service.py      # 配置业务逻辑
│       ├── health/             # 健康检查
│       │   └── router.py       # GET /api/health
│       └── data_sources/       # 外部数据接入（Phase 2a+）
│           ├── models.py       # DataSource + ExternalItem ORM
│           ├── schemas.py      # 数据源 Pydantic schema
│           ├── registry.py     # DataSource 注册表
│           ├── router.py       # 数据源管理 API
│           ├── service.py      # 数据源业务逻辑
│           └── ingestion/      # 接入管道
│               ├── connector.py         # DataSourceConnector ABC + RawDocument
│               ├── pipeline.py          # IngestionPipeline（扫描 → 索引状态机）
│               ├── store.py             # IngestionStore（SQLite 表存储，dedup 状态）
│               ├── parser.py            # 文档解析（PDF/Word/Markdown）
│               ├── retry.py             # jittered_retry 装饰器
│               ├── document_index_provider.py  # DocumentIndexProvider 抽象
│               ├── provider_factory.py  # Provider 工厂（LanceDB / Null）
│               ├── connectors/
│               │   └── local_folder.py  # LocalFolderConnector（.md/.txt 扫描 + watchdog）
│               └── providers/
│                   ├── lancedb.py       # LanceDB 向量存储（语义搜索实现）
│                   └── null.py          # 空实现（缺省降级）
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
| `GET` | `/api/health` | 健康检查（含 DocumentIndexProvider 状态） |
| `POST` | `/api/chat` | SSE 流式对话（Agent Loop），body: `{ message, conversation_id?, user_id? }` |
| `GET` | `/api/chat/history?user_id=&limit=` | 对话历史列表 |
| `GET` | `/api/chat/{conversation_id}` | 单条会话消息详情 |
| `DELETE` | `/api/chat/{conversation_id}` | 删除对话及其消息 |
| `GET` | `/api/memory/me` | 读取用户画像记忆内容 |
| `GET` | `/api/memory/stats?user_id=` | 记忆统计（状态、事件数） |
| `GET` | `/api/memory/list?user_id=` | 记忆列表 |
| `POST` | `/api/memory/reset?user_id=` | 清空用户记忆 |
| `POST` | `/api/memory/rebuild` | 重建记忆（.md + FTS5 + Provider 语义索引） |
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

- **Agent 系统**：PydanticAI 实现（`modules/agent/pydantic_agent.py`），支持工具调用、动态系统提示词、流式输出
- **工具系统**：分层架构 — `ToolDefinition`（元数据）→ `ToolRegistry`（注册表）→ `ToolDispatcher`（分发）→ `Toolset`（组合），内置工具在 `modules/agent/tools/builtin/`，安全策略在 `modules/agent/tools/core/policies.py`
- **LLM 路由**：`pydantic_agent.py` 内 `_create_model()` 支持多 Provider（DashScope/OpenAI/DeepSeek 等），通过 LiteLLM 统一调用
- **记忆层**：双管线 — Profile 事件（→ `.md` 投影）+ Narrative 事件（→ FTS5 `growth_events_fts`）；LanceDB 提供语义搜索（`DocumentIndexProvider` 可插拔）；`external_items` 表（Phase 2a）存储外部文档索引
- **数据库**：SQLite（`lumen.db`），`core/migrations.py` 集中管理所有 DDL（含 FTS5 虚拟表与触发器），`lifespan` 中调用 `migrate_sqlite()`
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

## 已移除端点（Workstream C 重构）

以下端点已在架构重构中移除，前端不再调用：

- ~~`GET /api/memory/profile-structured`~~ → 由 `/api/memory/understanding` 替代
- ~~`POST /api/memory/profile-update`~~ → 由 `/api/memory/understanding/correct` 替代
- ~~`POST /api/memory/upload-resume`~~ → 简历解析服务已移除

## Code Style

- Python 3.11+，类型提示（`from __future__ import annotations`）
- SQLAlchemy 2.0 async（`Mapped[...]`, `mapped_column()`）
- Pydantic v2（`BaseSettings`, `BaseModel`）
- 前端 React 19 + TypeScript + Tailwind CSS 4
- ruff 做 lint + format（`pyproject.toml` 配置）
- pytest + pytest-asyncio（30 条测试，`pytest` 运行）

## Gotchas

- `chat/service.py` 流式对话使用 `db.commit()` 而非 `flush()`，确保用户消息立即落库，流中断不丢失
- `update_profile` 中 `null` 可以清空字段（通过 `model_fields_set` 区分"未传"和"传 null"）
- `chatSession.tsx` 使用 `sessionStorage` 持久化 conversationId，刷新页面不丢失对话
- `modules/agent/pydantic_agent.py` 的 `_tool_runtime` 是全局缓存，配置变更后需清空缓存，否则工具列表不会刷新

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
