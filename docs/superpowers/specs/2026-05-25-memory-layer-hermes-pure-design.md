# 记忆层 Hermes-Pure 重构设计

> 日期：2026-05-25  
> 状态：设计已确认，可进入计划阶段  
> 范围：用 Hermes 风格的文件优先记忆系统，替换 Lumen 当前基于 `GrowthEvent` 的记忆运行时。

## 摘要

Lumen 将移除 `GrowthEvent` 作为运行时记忆层，改为让 Markdown 文件成为长期记忆的唯一真实来源。

新架构原则参照 Hermes agent：

- 内置文件记忆永远可用；
- 外部记忆是可选能力，通过一个当前激活的 `MemoryProvider` 接入；
- Agent 上下文从稳定文件和 provider 预取结果中组装；
- 运行时记忆不再依赖 SQLite 事件行、FTS5 投影、向量补偿循环或事件审核状态。

因为当前项目仍处于开发阶段，**不需要迁移旧记忆数据**。现有 `growth_events` 数据可以直接丢弃。实现目标是移除运行时代码引用，而不是兼容旧数据。

## 目标

1. 从运行时代码中删除 GrowthEvent 记忆管线。
2. 保留 `memory.md` 和 `about_you.md` 作为持久记忆文件。
3. 将记忆工具、API 路由、上下文注入、前端记忆 UI 改成文件优先模型。
4. 增加 Hermes 风格的 `MemoryProvider` 接口，用于可选外部召回和同步。
5. 保持 Agent 行为简单：重要事实写入 `memory.md`，`about_you.md` 由 `memory.md` 生成，有 provider 时预取外部上下文。
6. 输出适合交给 Kimi 编码执行的清晰分阶段任务。

## 非目标

- 不保留旧 `GrowthEvent` 行。
- 不保留事件 ID、逐条编辑/删除、confirmed/rejected 审核语义。
- 不保留 FTS5 记忆搜索能力。
- 不保留当前依赖事件推导的 observations、journey、intents、now-status。
- 不照搬 Hermes CLI 命令或具体 provider 实现。
- 不引入 `memory_index.json` 之类第二 truth source。

## 目标架构

```text
memory_save / tellAI / update_profile
        ↓
直接更新 ~/.lumen/memory/{user_id}/memory.md
        ↓
后台刷新 about_you.md
        ↓
会话启动：把 about_you.md（+ memory.md）作为冻结快照注入 system prompt（L0，全程不变）
        ↓
每轮 build_context（动态，<memory-context> 围栏）：
  L1：近期对话（本就在消息历史里，无需额外注入）
  L2：MemoryProvider.prefetch(query)，无 provider 时为空
```

`memory.md` 是可编辑的长期记忆文档。`about_you.md` 是由 `memory.md` 生成的 AI 用户画像。

**L0 走冻结快照（对齐 Hermes）**：`about_you.md`（缺失时回退到 `memory.md`）在会话启动时取一次快照、注入 system prompt，会话内不再变化；mid-session 的 `memory_save` 写盘但不改 system prompt（保护 prefix cache），新内容下次会话启动时生效。每轮只动态注入 L2（外部 provider 召回）。

外部召回是可选能力，不能成为应用启动或聊天的前置条件。

## 新记忆模块

新的 `lib/memory/` 应收敛到更小的模块集合：

| 模块 | 职责 |
|---|---|
| `provider.py` | 定义 Hermes 风格的 `MemoryProvider` 接口 + `NoOpMemoryProvider`。 |
| `manager.py` | 进程级 `MemoryManager`：内置文件记忆 + 一个当前激活的外部 provider；fan-out 编排。 |
| `builtin_provider.py` | `BuiltinMemoryProvider`：文件-backed 有界存储；`system_prompt_block()` 返回 L0 冻结快照。 |
| `loader.py` | 从 `~/.lumen/plugins/memory/<name>/` 发现 provider 插件。 |
| `markdown.py` | `AsyncMarkdownStore`：对 `memory.md` 和 `about_you.md` 做原子读写，per-user 写锁 + 跨进程文件锁。 |
| `context_fence.py` | `build_memory_context_block()` + `sanitize_context()` + `StreamingContextScrubber`。 |
| `understanding.py` | 从 `memory.md` 生成、读取、纠正 `about_you.md`。 |
| `snapshot.py` | 薄兼容层，委托 `MemoryManager.build_context()`（保留函数签名）。 |

公开 import 面应暴露 manager 风格 API，而不是 `LumenMemory` 事件方法。

## MemoryProvider 接口

Provider 接口**完整复刻 Hermes 的插件子系统**（不是精简的 9 方法版本）：核心生命周期 + 工具 schema 路由 + 可选生命周期钩子 + 插件 loader。详细签名见实施计划「记忆插件子系统」章节，此处给出核心方法。

> **async 约定（Lumen 专属）**：Lumen 是 async-native 的 FastAPI 应用，因此除 `name`（property）外，接口方法**全部为 `async`**——这一点与 Hermes 的同步接口不同，是 Lumen 的有意取舍。

```python
class MemoryProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    # 核心生命周期
    async def is_available(self) -> bool: ...
    async def initialize(self, session_id: str, **kwargs) -> None: ...
    async def system_prompt_block(self) -> str: ...        # builtin 在此返回 L0 冻结快照
    async def prefetch(self, query: str, *, session_id: str = "") -> str: ...
    async def queue_prefetch(self, query: str, *, session_id: str = "") -> None: ...
    async def sync_turn(self, user: str, assistant: str, *, session_id: str = "") -> None: ...
    async def get_tool_schemas(self) -> list[dict]: ...    # 外部 provider 暴露自己的工具
    async def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str: ...
    async def shutdown(self) -> None: ...

    # 可选钩子（子类重写以启用）
    async def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None: ...
    async def on_pre_compress(self, messages: list) -> str: ...
    async def on_session_end(self, messages: list) -> None: ...
    async def on_session_switch(self, new_session_id: str, *, reset: bool = False, **kwargs) -> None: ...
    async def on_memory_write(self, action: str, target: str, content: str, metadata: dict | None = None) -> None: ...
    async def on_delegation(self, task: str, result: str, **kwargs) -> None: ...
    async def get_config_schema(self) -> list[dict]: ...
    async def save_config(self, values: dict, lumen_home: str) -> None: ...
```

Provider 失败必须隔离。坏掉的 provider 只能记录 warning 并降级到内置文件记忆，不能打断聊天。

同一时间只激活一个外部 provider。内置文件记忆（`"builtin"`）始终激活。

## 文件与数据模型

每个用户对应：

```text
~/.lumen/memory/{user_id}/
  memory.md       # 可编辑长期记忆；唯一 truth source
  about_you.md    # 生成或手动纠正的用户画像
```

运行时不再有记忆表。不再有记忆 FTS 表。不再有事件投影缓存。

`memory.md` 必须原子写入。来自 Web、CLI、Telegram 或后台 review 的并发写入必须通过 per-user async lock 串行化，避免覆盖彼此内容。

### 记忆作用域与会话语义（Lumen 多渠道）

Lumen 是常驻 asyncio 进程：`MessageBus` → `AgentRunner` 消费循环 → pydantic-ai Agent → `EventBus` → 各 channel（web SSE / telegram / cli）+ presence 主动行为。FastAPI 只是 web channel 的传输层。由此确定记忆作用域：

- **文件按 `user_id`**：`memory.md` / `about_you.md` 是伙伴对"你"的记忆，跨渠道共享一份。
- **召回 / 会话生命周期按 `session_key`**（bus 事件的 `channel` + `chat_id`）：外部 provider 的 L2 召回和 `on_session_switch` 按此隔离，避免 web 与 telegram 串台。
- **L0 冻结快照按 conversation（`chat_id`）冻结**：进入一段对话取一次快照，对话内多轮复用同一份 system prompt（命中 prefix cache）；mid-conversation 写盘不改该对话 prompt，下一段对话才刷新。
- **主动行为同路径**：presence 驱动的主动推送是无用户输入的 agent-run，同样经 L0 注入。
- `MemoryManager` 为进程级单例，供 bus / AgentRunner / 各 channel / 后台任务共享，不绑定单次请求。

## 工具行为

### `memory_save`

工具直接写入 `memory.md`。

第一版实现可以把内容追加为带日期的 bullet，放到一个宽泛章节下；不需要一开始就维护复杂 Markdown parser。优先保证持久、可读、不会丢。

建议格式：

```markdown
## Long-term notes

- 2026-05-25 — [preferences] 用户偏好直接、面向实现的回答。
```

写入后，触发或安排 `about_you.md` 刷新，沿用现有 5 分钟防抖语义即可。

### `memory_search`

搜索顺序：

1. 当前激活的外部 provider：`prefetch(query)`；
2. 本地 `memory.md` 简单文本/段落匹配；
3. 返回空结果提示。

不返回事件 ID。

### `get_profile` / `update_profile`

`get_profile` 从 `about_you.md` / `memory.md` 读取当前上下文。

`update_profile` 将基础画像事实直接写入 `memory.md`，不创建事件。

## API 行为

### 保留并改造

| Endpoint | 新行为 |
|---|---|
| `GET /api/memory/me` | 返回完整 `memory.md`。 |
| `PUT /api/memory/me` | 保存完整 `memory.md`。 |
| `POST /api/memory/reset` | 清空 `memory.md` 和 `about_you.md`。 |
| `GET /api/memory/understanding` | 返回 `about_you.md`；非文件推导字段可为空。 |
| `POST /api/memory/understanding/refresh` | 从 `memory.md` 重新生成 `about_you.md`。 |
| `POST /api/memory/understanding/correct` | 覆写 `about_you.md`。 |
| `POST /api/memory/tell` | 将用户主动提供的文本追加/合并到 `memory.md`。 |
| `GET /api/memory/search` | provider/file 搜索，不再有事件 ID 语义。 |

### 删除或退役

- `GET /api/memory/list`
- `DELETE /api/memory/{event_id}`
- `PATCH /api/memory/{event_id}`
- `POST /api/memory/{event_id}/review`
- `GET /api/memory/observations`

如果实现过程中需要短暂前端兼容，退役接口可以暂时返回安全空结果；但最终代码不能保留事件管理语义。

## 前端行为

### Memories 页面

把逐条事件列表替换为完整 `memory.md` 编辑器：

- 加载记忆全文；
- 编辑文本；
- 保存文本；
- 重置记忆；
- 刷新 AI 理解。

### Settings 记忆区域

删除事件审核、逐条编辑、逐条删除控件。改为复用全文编辑器，或直接跳转到 Memories 页面。

### Profile 页面

保留：

- “关于你”展示；
- “关于你”纠正；
- “告诉 Lumen”表单；
- 刷新理解；
- 重置记忆。

第一阶段隐藏或移除：

- `你的心愿` / intents；
- `此刻` / now status；
- `你走过的路` / journey。

这些区块当前依赖事件时间线，第一版不要从平面文件里硬造。

### ObservationStrip

删除或禁用。旧接口依赖 `GrowthEvent`。

### QuickNotes

**直接删除 notes 功能**（2026-05-25 产品决策，覆盖早期"剥离为独立模块"的设想）。删除 `server/routes/notes.py`、`lib/tools/notes.py` 及对应前端组件。这同时也移除了 `quick_note` 对 `GrowthEvent` 的依赖。

## 运行时接入

### Agent 上下文

`lib/chat/agent_runner.py` 分两个注入点：

**进入对话（按 `chat_id` 冻结进 system prompt，该对话全程不变）**：

```text
system_prompt = base_prompt + memory_manager.build_system_prompt()
# build_system_prompt() 含 builtin 的 L0 冻结快照（about_you.md，缺失时回退 memory.md）+ 外部 provider 自我介绍
# 快照随会话状态按 chat_id 缓存，对话内多轮喂同一份 → 命中 prefix cache
```

**每轮（动态，<memory-context> 围栏，追加在消息序列里）**：

```text
context = memory_manager.build_context(user_id, user_input, session_key=session_key)
```

每轮动态上下文应包含：

- 当前时间，保持现状；
- L2：provider prefetch（按 `session_key` 隔离），如果已配置（builtin 的 prefetch 返回空，其内容已在 L0 冻结快照里）。

L0 不在每轮重复注入；L1（近期对话）本就在消息历史中。AgentRunner 处理的任何 run（用户触发或 presence 主动推送）都走此路径。

### 轮次同步

一次成功的 assistant turn 结束后调用：

```text
memory_manager.sync_all(user_message, assistant_response, session_key=session_key)
```

这让外部 provider 有机会摄取对话轮次。内置文件记忆不应自动保存每轮对话；它仍应依赖有意图的 `memory_save`、`tellAI` 或后台 review。

### 压缩 hook

对话摘要/压缩前调用：

```text
memory_manager.on_pre_compress(messages)
```

将 provider 返回文本追加到压缩 prompt。Lumen 中这个接入点更可能在 summary/compression service，而不只是 `lib/chat/session.py`。

### 关闭

FastAPI shutdown 时通过 manager 调用 provider `shutdown()`。

## 删除清单

最终代码不应 import 或依赖：

- `lib.memory.models.GrowthEvent`
- `lib.memory.facade.LumenMemory`
- `lib.memory.writer`
- `lib.memory.classifier`
- `lib.memory.events_merger`
- `lib.memory.relational_store`
- `lib.memory.projection`
- `lib.memory.searcher`
- `lib.memory.search`
- `lib.memory.observations`
- `core.vector_store`

`core/migrations.py` 应停止创建 GrowthEvent 记忆 FTS 表和触发器。`lib/model_registry.py` 应停止注册 `GrowthEvent`。

已有文件可以按实现需要删除或替换。硬性不变量是：运行时代码不再依赖 GrowthEvent。

## 数据迁移

不做迁移。

项目仍处于开发阶段，用户明确接受丢弃现有记忆数据。

实现可以让旧 SQLite 表留在磁盘里，但应用不能再读写它们。干净数据库启动时不应再创建 `growth_events` 记忆运行时结构。

## 测试策略

后端测试：

- `memory_save` 写入 `memory.md`。
- 无 provider 时 `memory_search` 正常工作。
- `GET /api/memory/me` 读取完整 Markdown。
- `PUT /api/memory/me` 保存完整 Markdown。
- `POST /api/memory/tell` 更新 Markdown。
- `POST /api/memory/reset` 清空记忆文件。
- `POST /api/memory/understanding/refresh` 读取 `memory.md` 并写入 `about_you.md`。
- notes 路由/工具已删除（`/api/notes` 不存在）。
- 应用启动不再 import `GrowthEvent`。
- provider 失败不会中断聊天。
- `about_you.md` 作为 L0 冻结快照进 system prompt；mid-session `memory_save` 写盘但不改 system prompt。

前端测试/检查：

- Memories 编辑器可以加载和保存。
- Settings 不再调用事件删除/审核/编辑接口。
- Profile 能处理空的 `patterns`、`intents`、`now_status`、`journey`。
- notes 相关前端组件已移除，不再有调用 `/api/notes` 的代码。

全局验证：

- grep 确认没有 `GrowthEvent` 运行时引用。
- Python 测试通过。
- TypeScript typecheck/build 通过。
- 后端可以干净启动。

## 给 Kimi 的实现约束

编码阶段可以交给 Kimi。实施计划应明确、删除导向：

1. 增加新的 file-first manager/provider seam。
2. 将 memory tools 和 profile tools 改到 manager。
3. 改造 memory routes 和前端记忆 UI。
4. 删除 notes 功能（路由 + 工具 + 前端组件）。
5. 改造 understanding/snapshot/context 路径。
6. 删除 GrowthEvent 模块、migrations、registry 引用和旧测试。
7. 运行完整验证并修复所有残留 import。

Kimi 不应保留双 truth source。任何临时兼容都必须在最终验证前删除。

## 已确认决策

高层产品决策已经明确：

- 采用激进 GrowthEvent 删除；
- 丢弃旧数据，不做迁移；
- 用全文编辑代替逐条事件管理；
- 参照 Hermes 架构原则，但保留 Lumen 文件名和产品 UI。

2026-05-25 补充决策（实施计划阶段敲定）：

- **采用完整 Hermes 插件子系统**（tool-schema 路由、`plugin.yaml`、prefetch fan-out、`on_memory_write` 镜像、完整生命周期钩子），而非精简 9 方法接口；
- 接口除 `name` 外**全部 async**（Lumen async-native，区别于 Hermes 的同步接口）；
- `memory.md` 条目用 **Markdown bullet** 追加到 `## Long-term notes` 下（非 Hermes 的 `§` 分隔）；
- **删除 notes 功能**（不剥离为独立模块）；
- **L0（`about_you.md`）走冻结快照**注入 system prompt，**按 conversation（`chat_id`）冻结**：对话内多轮复用同一份，mid-conversation 写盘不更新、下一段对话才刷新——对齐 Hermes 的 frozen-snapshot + prefix-cache 语义；
- **记忆作用域**：文件按 `user_id`（跨渠道共享），召回/会话生命周期按 `session_key`（`channel`+`chat_id`）；主动行为走同一注入路径。

剩余选择都是实施计划阶段的工程细节。
