# AGENTS.md

## 定位

Lumen — 一个真正认识你的 AI 伙伴。单用户长期记忆 AI 产品。

**技术栈**: FastAPI + SQLAlchemy + PydanticAI + SQLite | React 19 + Vite + Tailwind CSS 4 | Tauri v2 (Rust)

---

## Quick Start

```bash
pip install -r requirements.txt && npm install
# 配置 .env: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
.\start.ps1          # Web 开发模式
cargo tauri dev      # 桌面模式（推荐）
```

---

## 核心架构

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

1. **主动写入**：Agent 调用 `memory_save` 工具（或 `tellAI` / `update_profile`）→ 追加带日期的 bullet 到 `MEMORY.md` 的 `## Long-term notes` 章节
2. **画像刷新**：写入后台触发 `understanding.py` 从 `MEMORY.md` 重新生成 `USER.md`（5 分钟防抖）
3. **被动兜底**：`review_service.py` 在对话结束后 fork 一个 Agent 审查本轮，判断是否有值得写入 `MEMORY.md` 的内容
4. **外部召回 / Provider 插件化**：如需语义检索，把 `MemoryProvider` 插件放到 `lib/memory/builtins/<name>/`（内置）或 `~/.lumen/plugins/memory/<name>/`（用户）；启动时按 `~/.lumen/config.json["memory_providers"]` 动态加载，支持多个同类型实例并存。`MemoryManager` 在 L2 prefetch 时 fan-out 调用所有外部 provider
5. **定期整理**：`MemoryHousekeeper`（`lib/memory/housekeeping.py`）24 小时循环清理：`[intent]` 30 天后标 stale、`[transient]` 7 天后删除、`[fact]`/`[preference]` 永不过期

### 开发 CLI TUI

CLI TUI 是独立的 TypeScript 项目，运行时直接 HTTP 连接 Lumen 后端。

**启动开发模式：**
```bash
cd channels/cli
bun run dev         # 启动 TUI，连接默认端口 8000
LUMEN_PORT=8080 bun run dev   # 指定后端端口
```

**关键文件：**

| 文件 | 职责 |
|------|------|
| `lumen/api.ts` | Lumen REST + SSE 客户端（`sendMessage`, `listConversations`, `getConfig`, `getTUICommands`, `executeCommand`...） |
| `context/sdk.tsx` | Lumen API 适配器，将 SSE 事件广播为 `LumenEvent`（`token` / `thinking` / `trace` / `response.done`） |
| `context/sync.tsx` | OpenCode store 适配，处理 `LumenEvent` → `store.message` / `store.part`，SolidJS 细粒度响应；启动时加载斜杠命令 |
| `context/local.tsx` | model / agent / session 本地状态，在 mount 时从 `/api/config` 拉取实际 provider+model |
| `component/prompt/index.tsx` | 输入框组件，含斜杠命令解析与分发（`/resume` `/delete` → 会话列表对话框） |
| `component/dialog-session-list.tsx` | 会话列表对话框，支持模糊搜索、删除（双击 D）、重命名、置顶 |
| `routes/session/index.tsx` | 对话页面，渲染 `UserMessage` / `AssistantMessage`，含 thinking 和 tool-invocation part |
| `component/logo.tsx` | 品牌 Logo，含 ripple 点击、hold-charge、idle shimmer 动画 |
| `logo.ts` | Logo 字形数据（block 字体风格，LU 暗色 + MEN 亮色） |

**SolidJS 响应式关键约束：**
- Part 存储在 `store.part[messageID]`（独立于 message），使用 `setStore("part", msgID, partIdx, "text", val)` 直接路径更新才能触发细粒度响应
- `produce()` 深层突变对标量值不可靠，只用于向数组追加元素
- 流式 token 累积在 `streamingText Map`，每次 token 事件都更新 `store.part`

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
| `skill-creator` | `skill_load` | 创建和优化 Skills |

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

### 开发 Memory Provider 插件

Lumen 支持两种插件位置：

1. **内置插件**：`lib/memory/builtins/<name>/`
   - 随仓库分发
   - 适合官方维护的 provider（如 honcho）
2. **用户插件**：`~/.lumen/plugins/memory/<name>/`
   - 用户自行开发/安装
   - 同名时覆盖内置插件

目录结构：

```
<name>/
├── plugin.yaml       # 插件元数据
└── __init__.py       # Provider 实现
```

`plugin.yaml` 示例：

```yaml
name: honcho
version: "0.1.0"
description: "Honcho AI external memory provider"
class: Provider
```

`__init__.py` 示例：

```python
from lib.memory.provider import MemoryProvider

class Provider(MemoryProvider):
    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    @property
    def name(self) -> str:
        return "my-provider"

    async def is_available(self) -> bool:
        return bool(self.api_key)

    async def initialize(self, session_id: str, **kwargs) -> None:
        pass

    async def system_prompt_block(self, **kwargs) -> str:
        return ""

    async def prefetch(self, query: str, *, session_id: str = "", **kwargs) -> str:
        return ""

    async def sync_turn(self, user: str, assistant: str, *, session_id: str = "") -> None:
        pass

    async def get_tool_schemas(self) -> list[dict]:
        return []

    async def on_memory_write(self, action, target, content, metadata=None) -> None:
        pass
```

配置启用：

```json
{
  "memory_providers": [
    {
      "name": "my-honcho",
      "provider_type": "honcho",
      "enabled": true,
      "config": {
        "api_key": "..."
      }
    }
  ]
}
```

**多实例并存**：`name` 是配置实例名（如 `honcho-prod`、`honcho-dev`），`provider_type` 是插件类型。多个配置可以共用同一个 `provider_type`，只要 `name` 不同就不会互相覆盖。`MemoryManager` 用 `name` 作为内部 key，用 `provider.display_name` 标注 L0/L2 结果。

**内置 Akasha 插件**：`akasha` 是一个图记忆引擎，配置示例：

```json
{
  "memory_providers": [
    {
      "name": "akasha",
      "provider_type": "akasha",
      "enabled": true,
      "config": {
        "dense_top_k": 10,
        "ripple_top_k": 10,
        "activate_limit": 8
      }
    }
  ]
}
```

Akasha 需要 embedding 配置才能工作。默认 sidecar DB：`~/.lumen/memory/{user_id}/akasha.db`。

---

## Reference

### 项目结构

```
Lumen/
├── main.py                     # FastAPI 入口，lifespan 中自动建表 + 初始化
├── lumen.py                    # 统一启动入口（--mode web/cli/all/telegram）
├── core/                       # 基础设施
│   ├── config.py               # pydantic-settings，从根目录 .env + ~/.lumen/config.json 加载
│   ├── db.py                   # SQLAlchemy AsyncEngine + Base + get_async_session_maker
│   ├── migrations.py           # SQLite 兼容迁移、FTS5 表、触发器、MD 文件命名迁移
│   ├── startup.py              # 启动初始化（建表、记忆 Provider 插件加载、Channel 启动、关闭清理）
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
│   ├── chat/                   # 对话模块
│   │   ├── agent_runner.py     # AgentRunner：后台消费消息，运行 Agent Loop
│   │   ├── session.py          # 会话状态管理
│   │   ├── persistence.py      # 消息持久化
│   │   ├── session_files.py    # 会话附件管理
│   │   ├── lock.py             # 对话并发锁
│   │   ├── summary.py          # 对话摘要后台任务
│   │   ├── agent_trace.py      # AgentTrace 可观测性
│   │   └── event_handlers.py   # Agent 事件处理（流式输出、工具调用跟踪）
│   ├── memory/                 # 记忆层（Hermes-Pure 文件优先：MEMORY.md 唯一真相源）
│   │   ├── provider.py         # MemoryProvider 抽象接口 + NoOpMemoryProvider
│   │   ├── manager.py          # MemoryManager 进程级单例（内置文件记忆 + 外部 provider 编排）
│   │   ├── builtin_provider.py # BuiltinMemoryProvider（文件 backed，L0 冻结快照）
│   │   ├── builtins/           # 内置记忆 Provider 插件
│   │   │   ├── honcho/         # HonchoProvider 插件（语义召回 + 轮次同步）
│   │   │   └── akasha/         # Akasha 图记忆引擎插件
│   │   ├── models.py           # MemoryProviderConfig 配置模型
│   │   ├── config_store.py     # memory_providers 配置持久化
│   │   ├── housekeeping.py     # MemoryHousekeeper（24h 循环清理过期 intent/transient 条目）
│   │   ├── loader.py           # 从 ~/.lumen/plugins/memory/ 发现 provider 插件
│   │   ├── markdown.py         # AsyncMarkdownStore：MEMORY.md / USER.md 原子读写
│   │   ├── context_fence.py    # <memory-context> 围栏构建 + 注入内容清洗
│   │   ├── snapshot.py         # 薄兼容层，委托 MemoryManager 构建 L0 + L1 近期对话
│   │   ├── understanding.py    # AI 综合画像生成（MEMORY.md → USER.md，5 分钟防抖）
│   │   └── review_service.py   # 后台记忆审查（Agent fork 审查对话，写入 MEMORY.md）
│   ├── profile/                # 画像模块
│   │   ├── models.py           # User + UserProfile（通用伙伴画像）
│   │   └── schemas.py          # ProfilePayload, KeyValuePayload, DecisionPayload
│   ├── partner/                # 伙伴系统（情绪、内心独白）
│   │   ├── models.py           # LumenState + LumenThought ORM
│   │   └── mood_inference.py   # 情绪推断逻辑
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
│       ├── _path_safety.py     # 文件路径黑名单安全模型（凭据目录、系统目录）
│       ├── factory.py          # 工具工厂（注册所有内置工具 + MCP 工具）
│       ├── skill_load.py       # skill_load 工具（动态加载 Skill）
│       ├── memory.py           # memory_search, memory_save
│       ├── profile.py          # get_profile, update_profile
│       ├── shell.py            # Shell 命令执行工具
│       ├── files.py            # 文件工具：读 / 写 / 列目录 / grep / 局部编辑（file_edit）
│       ├── web_search.py       # 网络搜索（需配置 SEARCH_PROVIDER）
│       ├── web_tools.py        # 多后端网页搜索 + 内容提取（Exa/Tavily/Serper/Brave/DDG）
│       ├── delegate.py         # 子 Agent 委派工具（隔离上下文、工具白名单/黑名单）
│       ├── vision.py           # 图片分析工具（PNG/JPEG/GIF/WebP，自动压缩，VL 模型）
│       └── mcp/                # MCP 工具桥接
│           ├── client_manager.py
│           ├── config_store.py
│           ├── models.py
│           ├── tool_bridge.py
│           └── transport.py
├── channels/                   # 多渠道实现（顶级包）
│   ├── base.py                 # BaseChannel 抽象基类
│   ├── web/                    # Web 渠道
│   │   ├── web.py              # WebChannel：SSE 流式对话
│   │   └── formatters.py       # SSE 事件格式化
│   ├── telegram/               # Telegram 渠道
│   │   ├── channel.py          # TelegramChannel：Polling 模式
│   │   ├── handlers.py         # Bot 消息处理器
│   │   ├── telegram_utils.py   # Telegram 工具函数
│   │   └── downloader.py       # 文件下载处理
│   ├── desktop/                # Tauri v2 桌面壳（Rust）
│   │   └── src-tauri/
│   │       └── src/
│   │           ├── lib.rs      # start_backend / stop_backend
│   │           └── main.rs
│   └── cli/                    # CLI TUI（独立 TypeScript/Bun 项目）
│       └── cmd/tui/
│           ├── package.json    # Bun 依赖（@opentui/solid、SolidJS）
│           ├── lumen/api.ts    # Lumen FastAPI REST + SSE 客户端
│           ├── context/        # SolidJS 响应式数据层（sdk/sync/local 等 19 个文件）
│           ├── routes/         # 页面路由（home / session）
│           ├── component/      # 通用 UI 组件（logo/prompt/dialog 等 29 个文件）
│           └── stubs/          # OpenCode SDK 接口桩（适配 Lumen 后端）
├── mcp_servers/                # 内置 MCP 服务器（当前为空）
├── server/                     # API 路由层
│   └── routes/
│       ├── chat.py             # POST /api/chat (SSE), GET /api/chat/history
│       ├── memory.py           # 记忆管理 API（文件优先）
│       ├── memory_providers.py # Memory Provider 配置与加载 API
│       ├── akasha.py           # Akasha 检索记录 Dashboard API
│       ├── config.py           # 配置 API
│       ├── health.py           # GET /api/health
│       ├── providers.py        # Provider 管理 API
│       ├── mcp.py              # MCP 服务器管理 API
│       ├── commands.py         # 斜杠命令 API（list / execute）
│       └── partner.py          # 伙伴系统 API（情绪状态）
├── src/                        # React 前端（Vite）
│   ├── App.tsx
│   ├── main.tsx                # 路由配置
│   ├── index.css
│   ├── pages/
│   │   ├── Chat.tsx            # SSE 流式对话 + 历史抽屉 + 思考过程
│   │   ├── Profile.tsx         # AI 综合画像 + 主动塑造 + 模式/心愿/此刻/时间线
│   │   ├── InnerWorld.tsx      # 伙伴内心状态（建设中）
│   │   └── Settings.tsx        # Provider 选择 + API Key
│   ├── components/
│   │   ├── Card.tsx / EmptyState.tsx / ProfileActions.tsx / Sidebar.tsx
│   │   ├── MoodStrip.tsx       # 情绪条组件
│   │   ├── TitleBar.tsx        # 标题栏
│   │   ├── ui/                 # 通用 UI 原子组件（ComboInput / KeyInput / Toggle）
│   │   └── providers/          # Provider 管理组件（ProviderList / ModelEditPanel 等）
│   └── lib/
│       ├── api.ts              # API 客户端统一导出
│       ├── chatSession.tsx     # 全局聊天状态管理
│       ├── userId.ts           # 用户 ID 管理
│       ├── thinkSegments.ts    # 思考片段处理
│       ├── store/              # 状态管理
│       │   └── providersStore.ts
│       └── api/                # API 模块拆分
│           ├── core.ts
│           ├── chat.ts
│           ├── memory.ts
│           ├── config.ts
│           ├── providers.ts
│           └── partner.ts
├── eval/                       # 评估框架（bench_memory / qa_runner / metrics）
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
| `PATCH` | `/api/chat/{conversation_id}` | 更新对话（重命名 title / 置顶 is_pinned） |
| `GET` | `/api/memory/me` | 读取用户画像记忆内容 |
| `PUT` | `/api/memory/me` | 保存完整 MEMORY.md 内容 |
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
| `GET` | `/api/akasha/overview` | Akasha 检索概览 |
| `GET` | `/api/akasha/turns` | Akasha 检索记录列表 |
| `GET` | `/api/akasha/turns/{query_id}` | 单条 Akasha 检索记录 |
| `GET` | `/api/memory/providers` | 列出已发现/已配置的记忆 Provider |
| `GET` | `/api/memory/providers/installed` | 列出已加载的记忆 Provider |
| `POST` | `/api/memory/providers` | 添加/启用记忆 Provider |
| `PUT` | `/api/memory/providers/{name}` | 更新记忆 Provider 配置 |
| `DELETE` | `/api/memory/providers/{name}` | 删除记忆 Provider 配置并卸载 |
| `POST` | `/api/memory/providers/{name}/test` | 测试记忆 Provider 连通性 |
| `POST` | `/api/memory/providers/{name}/reload` | 重新加载记忆 Provider |
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
| `GET` | `/api/memory/observations` | 观察事件列表（已退役） |
| `PATCH` | `/api/memory/{event_id}` | 更新指定记忆事件（已退役） |
| `POST` | `/api/memory/{event_id}/review` | 审查指定记忆事件（已退役） |
| `GET` | `/api/partner/mood` | 获取 Lumen 当前情绪状态 |
| `GET` | `/api/commands/list` | 列出可用斜杠命令 |
| `POST` | `/api/commands/execute` | 执行斜杠命令，body: `{ command, args?, session_id?, user_id? }` |

### 环境变量

根目录 `.env`（**不要提交到 git**）：

| 变量 | 说明 |
|------|------|
| `LLM_API_KEY` / `DASHSCOPE_API_KEY` | LLM 调用密钥 |
| `LLM_BASE_URL` | LLM API Base URL |
| `LLM_MODEL` | 模型名称 |
| `DATABASE_URL` | 默认 `~/.lumen/lumen.db` |
| `DEBUG` | `true`（开发）/ `false`（生产） |
| `HONCHO_API_KEY` | Honcho AI 外部记忆服务密钥（也可在 config.json["memory_providers"][*].config 中配置）|
| `HONCHO_WORKSPACE_ID` | Honcho workspace（默认 `lumen`）|
| `HONCHO_ENVIRONMENT` | Honcho 环境（默认 `production`）|
| `SEARCH_PROVIDER` | 搜索 Provider（tavily / serper / brave）|
| `SEARCH_API_KEY` | 搜索 API 密钥 |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token（存在时启用 Telegram 渠道）|

以下配置项通过 `~/.lumen/config.json` 管理（优先级高于 `.env`）：

| 配置字段 | 说明 |
|----------|------|
| `embedding_provider` / `embedding_model` | Embedding 模型（如 dashscope / text-embedding-v4）|
| `embedding_api_key` / `embedding_base_url` | Embedding 服务凭证 |
| `document_index_provider` | 文档索引后端（如 lancedb）|
| `vl_provider` / `vl_model` | 视觉语言模型（如 dashscope / qwen3-vl-flash）|
| `vl_api_key` / `vl_base_url` | VL 服务凭证 |
| `telegram_chat_id` | Telegram 推送目标 chat_id |
| `memory_providers` | 记忆 Provider 配置列表，每项 `{name, provider_type, enabled, config}` |

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

Lumen 通过统一的 Bus/Channel 层同时支持多种接入方式，后端业务逻辑与渠道完全解耦：

```
WebChannel (SSE)   TelegramChannel (Polling)   CLI TUI (独立 TypeScript/Bun 进程)
      │                    │                              │
      │                    │                    HTTP SSE → /api/chat
      └────────────────────┼──────────────────────────────┘
                           ↓
                   MessageBus (lib/bus/)
              InboundMessage → AgentRunner
              AgentRunner → OutboundMessage
                           ↓
                   EventBus (进程内 pub/sub)
        StreamDeltaReady / ToolCallStarted / TurnStarted / ...
```

- **BaseChannel**（`channels/base.py`）：抽象基类，`start()`/`stop()`/`send_message()`
- **WebChannel**（`channels/web/web.py`）：SSE 流式，前端 / Tauri 接入
- **TelegramChannel**（`channels/telegram/channel.py`）：Polling 模式，缓冲流式增量，整条发送
- **CLI TUI**（`channels/cli/`）：独立 TypeScript/Bun 项目，直接调用 Lumen REST API
  - 基于 OpenCode 的 opentui 框架（SolidJS + 终端渲染引擎）
  - `context/sdk.tsx`：Lumen API 适配层，将 SSE 事件转发到 OpenCode store
  - `context/sync.tsx`：OpenCode 数据层适配，将 Lumen 对话映射为 Session/Message/Part
  - 独立运行，无需 Python 进程间通信：`cd channels/cli && bun run dev`
- **AgentRunner**（`lib/chat/agent_runner.py`）：后台消费 MessageBus，驱动 PydanticAI Agent Loop

**Tauri 打包**（`channels/desktop/src-tauri/src/lib.rs`）：将 `python -m uvicorn main:app` 作为 sidecar subprocess 启动，通过 Windows Job Object 绑定生命周期，关闭窗口自动终止 Python 进程。Tauri 是分发壳，不影响 Channel 运行模式。

### 文件优先记忆架构（Hermes-Pure）

2026-05-25 重构：移除 GrowthEvent 事件管线，改为 Markdown 文件作为长期记忆的唯一真相源。运行时不再有记忆表、记忆 FTS、事件投影缓存或事件审核状态。

每个用户对应 `~/.lumen/memory/{user_id}/`：
- `MEMORY.md`：可编辑的长期记忆文档（唯一真相源，旧名 `memory.md` 已自动迁移）
- `USER.md`：由 `MEMORY.md` 生成的 AI 用户画像（旧名 `about_you.md` 已自动迁移）

写入路径：`memory_save` / `tellAI` / `update_profile` 直接写 `MEMORY.md` → 后台刷新 `USER.md`（见上文「写入长期记忆」）。

**作用域**：文件按 `user_id`（跨渠道共享一份记忆）；外部召回与会话生命周期按 `session_key`（`channel` + `chat_id`），避免 web 与 telegram 串台。

### L0 / L1 / L2 三层上下文

**L0 — 冻结快照（注入 system prompt）**
- `USER.md`（缺失时降级到 `MEMORY.md`），由 `MemoryManager.build_system_prompt()` 经 `BuiltinMemoryProvider` 读取（`snapshot.py` 是薄兼容层）
- 按 conversation（`chat_id`）冻结：进入对话取一次快照，对话内多轮复用同一份 system prompt（命中 prefix cache），5 分钟 LRU 缓存
- mid-conversation 的 `memory_save` 写盘但不改当前对话的 system prompt，下一段对话才生效

**L1 — 近期对话上下文**
- 最近 5 个对话、7 天内，每个对话最多 3 条消息（`snapshot.py` 的 `_fetch_recent_conversations`）
- 数据来源是 `Conversation + Message` 表，通过 `set_conversation_fetcher()` 依赖注入，解耦 memory 与 chat 模块
- 本就在消息历史里，无需额外重复注入

**L2 — 外部召回（按需，`<memory-context>` 围栏）**
- `MemoryProvider.prefetch(query)`，按 `session_key` 隔离
- `MemoryManager` 同时向所有外部 provider 发起 prefetch，结果按 `[provider.name]` 拼接
- 已启用的外部 provider 插件提供语义召回（如 `honcho` 通过 `peer.chat()`、`akasha` 通过图检索）
- 无外部 provider 时为空——内置文件记忆的内容已在 L0 冻结快照里

### 轮次同步与压缩

- 一次成功 assistant turn 后调用 `MemoryManager.sync_all(...)`，让外部 provider 摄取对话轮次；内置文件记忆不自动保存每轮，依赖有意图的 `memory_save` / `tellAI` / 后台 review
- 所有外部 provider 的 `sync_turn()` 都会收到轮次；`on_memory_write()` 将 `memory_save` / `update_profile` 的写入镜像到所有外部 provider
- 压缩前调用 `MemoryManager.on_pre_compress(messages)`，将 provider 返回文本追加到压缩 prompt

### 工具系统分层

工具系统采用注册表 + 动态发现模式：

1. **ToolDef**（元数据）— `lib/tools/_base.py` 中定义，含名称、描述、输入 schema、执行函数、风险等级、标签等
2. **注册表层** — `lib/tools/_registry.py` 的 `ToolRegistry` 管理全量工具索引，提供搜索和按名称过滤 schema 能力
3. **发现层** — `lib/tools/_discovery.py` 的 `ToolDiscoveryState` 按 `conversation_id` 维护预加载缓存（LRU），实现 deferred 工具的按需解锁
4. **工厂层** — `lib/tools/factory.py` 负责组装所有工具实例并注册到 `ToolRegistry`
5. **中间件层** — `lib/tools/_middleware.py` 提供通用横切能力
6. **具体工具** — `lib/tools/memory.py`, `profile.py`, `shell.py`, `files.py`, `web_search.py`, `web_tools.py`, `delegate.py`, `vision.py` 等
7. **MCP 桥接** — `lib/tools/mcp/` 将外部 MCP 服务器暴露为 Agent 工具

为什么这样设计？
- **动态发现**：非核心工具默认隐藏，Agent 需要通过 `tool_search` 解锁，减少首次调用的 schema 体积
- **扁平化注册**：新增工具只需在 `lib/tools/` 下新增模块并在 `factory.py` 注册
- **MCP 桥接**：任何兼容 MCP 的服务器都可被 Agent 调用，外部工具生态零成本接入
- **中间件层**：统一处理横切关注点（日志、重试、超时），业务工具只关注核心逻辑

### 接入外部 MCP Server

Lumen 核心不内置任何 MCP server，但可以通过 `~/.lumen/config.json` 的 `mcp_servers` 字段手动接入任何兼容 MCP 的服务器。接入后，server 提供的 tools 会自动出现在 Agent 的工具列表中。

配置示例（以 `lumen-rss` 为例）：

```json
{
  "mcp_servers": [
    {
      "name": "lumen-rss",
      "transport": "stdio",
      "command": "python",
      "args": ["E:/MyHub/Lumen/mcp_servers/rss/server.py"],
      "enabled": true,
      "auto_approve": true,
      "read_only": false
    }
  ]
}
```

也支持通过 REST API 动态管理：

- `GET /api/mcp/servers` — 列出已配置 server
- `POST /api/mcp/servers` — 添加 server
- `DELETE /api/mcp/servers/{name}` — 删除 server

设计原则：
- **零核心耦合**：MCP server 崩溃不影响 Lumen 核心运行
- **按需启用**：不配置就不加载，不占用资源
- **手动管理**：不由 `core/startup.py` 自动注册，避免隐性依赖

### 为什么用 SQLite + FTS5

- **零运维**：单文件，无需部署 PostgreSQL
- **FTS5 原生支持**：外部文档（`external_items`）全文搜索无需额外服务，CJK 用 trigram tokenizer
- **足够用**：单用户模式，数据量预期 < 100MB

长期记忆本身不再走 SQLite，而是文件优先（`MEMORY.md`）。SQLite 现承载对话/消息、外部文档索引、伙伴系统状态等。外部语义召回由 `MemoryProvider` 插件提供。

---

## Gotchas

- **CLI TUI 独立运行**：`channels/cli/` 是独立 TypeScript/Bun 项目，不通过 Python 进程间通信，直接 HTTP 连接后端。环境变量 `LUMEN_PORT` 控制后端端口（默认 8000）
- **CLI TUI SolidJS 响应式**：`store.part[msgID]` 需用直接路径 `setStore("part", msgID, i, "field", val)` 更新，`produce()` 深层突变对标量不触发细粒度响应
- **CLI TUI 光标闪烁**：opentui 原生 API 只支持开/关，不支持速率控制。速率由 `setInterval` 软件实现（600ms），在 `onCleanup` 中清理 timer
- `lib/chat/agent_runner.py` 流式对话在持久化时使用 `db.commit()`，确保用户消息立即落库，流中断不丢失
- `update_profile` 中 `null` 可以清空字段（通过 `model_fields_set` 区分"未传"和"传 null"）
- `chatSession.tsx` 使用 `sessionStorage` 持久化 conversationId，刷新页面不丢失对话
- `lib/tools/_registry.py` 的 `ToolRegistry` 和 `lib/tools/_discovery.py` 的 `ToolDiscoveryState` 是内存单例，配置变更后需重启后端，否则工具列表和发现状态不会刷新
- `understanding.py` 的 AI 画像生成有 5 分钟防抖（`_DEBOUNCE_SECONDS = 300`），频繁触发不会重复调用 LLM
- **记忆文件命名迁移**：`startup.py` 自动将旧名 `memory.md` → `MEMORY.md`、`about_you.md` → `USER.md`（`core/migrations.py` 的 `migrate_md_files()`）
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
