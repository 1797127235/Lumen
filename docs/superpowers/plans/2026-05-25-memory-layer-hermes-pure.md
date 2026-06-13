# 记忆层 Hermes-Pure 重构 — 实施计划

> 日期：2026-05-25  
> 对应设计文档：`docs/superpowers/specs/2026-05-25-memory-layer-hermes-pure-design.md`  
> 状态：**已完成**（2026-06-12 同步代码）  
> 目标：将 Lumen 记忆层从 `GrowthEvent` 模型迁移到 Hermes 风格的文件优先架构。  
> 数据迁移：**不做**。开发阶段，旧 `growth_events` 数据直接丢弃。  
> **Notes 功能**：本计划决定直接删除 notes（设计文档曾建议剥离为独立模块，经产品决策改为删除）。  
> 注意：下文为原始实施计划；与最终实现不一致处已在「实现偏差（2026-06-12）」章节标注。

---

## 阶段总览

| 阶段 | 任务 | 预计 PR 大小 | 可并行？ |
|---|---|---|---|
| 1 | 建立新 seam：provider + manager + markdown 原子读写 | 中 | 否（前置） |
| 2 | 改造工具层：`memory_save`、`memory_search`、`get_profile`、`update_profile` | 中 | 否（依赖阶段 1） |
| 3 | 改造 API 路由 + 前端记忆 UI | 大 | 部分可并（前端/后端拆分） |
| 4 | 改造 understanding / snapshot / context 注入路径 | 中 | 否（依赖阶段 1-2） |
| 5 | 删除 `GrowthEvent` 模块、migrations、registry 引用、旧测试 | 大 | 否（收尾） |
| 6 | 全局验证：grep 清理、测试、类型检查、启动 | 中 | 否（最终守门） |

## 实现偏差（2026-06-12）

相比原始实施计划，最终实现有以下扩展：

1. **多外部 provider 共存**：原计划在 `add_provider()` 中拒绝第二个外部 provider；实现改为用配置实例名 `name` 作为 key，支持同类型多实例并存，`prefetch_all()` / `sync_all()` / `on_memory_write()` 均 fan-out。
2. **内置插件目录**：除 `~/.lumen/plugins/memory/<name>/` 外，增加 `lib/memory/builtins/<name>/`；Honcho 与 Akasha 均作为内置插件维护。
3. **配置格式**：由 `config.yaml` 中单个 `memory.provider` 改为 `~/.lumen/config.json["memory_providers"]` 列表，旧 `honcho_enabled` / `HONCHO_API_KEY` 自动迁移。
4. **Akasha 迁移**：新增 `lib/memory/builtins/akasha/` 图记忆引擎插件，新增 `/api/akasha/*` 路由。
5. **REST 管理端点**：新增 `server/routes/memory_providers.py` 提供 `/api/memory/providers*` CRUD / test / reload。
6. **写入镜像**：`memory_save` / `update_profile` 写入 `memory.md` 后调用 `MemoryManager.on_memory_write()` 镜像到所有外部 provider。
7. **Embedding 客户端**：新增 `lib/llm/embeddings.py` 供 Akasha 等插件复用。

---

## 记忆插件子系统（Hermes 风格）

在阶段 1-2 中建立的新 seam 不是简单的"一个接口 + 一个管理器"，而是参照 Hermes agent 的完整**插件子系统**。以下是子系统的详细架构，应在阶段 1 中一并实现。

> **范围说明（2026-05-25 决策）**：本子系统刻意超出设计文档中描述的 9 方法精简接口，采用完整 Hermes 插件子系统（tool-schema 路由、`plugin.yaml`、prefetch fan-out、`on_memory_write` 镜像、生命周期钩子）。设计文档 `…-design.md` 的「MemoryProvider 接口」章节应同步更新以反映此扩展。
>
> **接口签名权威**：以本节「Provider 接口」中的 **async** 定义为准（除 `name` property 外全部为 `async`）。文档其他位置如有出入，以此为准。

### 记忆作用域与会话语义（Lumen 多渠道）

Lumen 不是请求-响应式 web 服务，而是一个常驻的 asyncio 进程：`MessageBus`（inbound 队列）→ `AgentRunner._run_loop()` → pydantic-ai Agent → `EventBus` 流式输出 → 各 channel（web SSE / telegram / cli）。FastAPI 只是 web 这一个 channel 的传输层。这决定了记忆层的作用域与冻结语义：

1. **文件按 `user_id`，召回与会话生命周期按 `session_key`**：
   - `memory.md` / `about_you.md` 是伙伴对"你"的记忆，**跨渠道共享一份**，路径维度是 `user_id`（无论你从 web 还是 telegram 说话，都是同一份）。
   - 外部 provider 的动态召回（L2）和会话生命周期（`on_session_switch` 等）是 **`session_key` 维度**（来自 bus 事件的 `channel` + `chat_id`），否则 web 与 telegram 的对话会在 Honcho/Mem0 里串台。

2. **L0 冻结快照按 conversation（`chat_id`）冻结**（2026-05-25 决策）：
   - 进入一段对话时取一次 `about_you.md`（+ `memory.md`）快照，组装进该对话的 system prompt；
   - **该对话内多轮复用同一份快照**，保证 system prompt 跨轮字节不变 → 命中 prefix cache；
   - mid-conversation 的 `memory_save` / 后台 review 写盘（持久）但**不改这段对话的 prompt**，新内容在**下一段对话**才生效；
   - 实现上：快照应随会话状态缓存（按 `chat_id`），AgentRunner 每轮喂同一份 system prompt，而非每轮重读文件。

3. **主动行为也走同一 L0 注入路径**：伙伴主动推送（presence 驱动）是**没有用户输入的 agent-run**，它同样需要 L0。"会话启动注入"覆盖 **AgentRunner 发起的任何 run**，不只是用户触发的那种。

4. **`MemoryManager` 为进程级单例**：bus、AgentRunner、各 channel、后台 review/presence 共享同一进程，需要进程内共享的记忆访问，不绑定任何单次请求。

### 插件目录结构

```text
~/.lumen/plugins/memory/
  <provider-name>/
    plugin.yaml          # 清单：名称、版本、描述、pip 依赖、钩子列表
    __init__.py          # 导出 Provider 类
```

Lumen 启动时，`lib/memory/loader.py` 扫描 `lib/memory/builtins/<name>/` 与上述用户目录，按 `~/.lumen/config.json["memory_providers"]` 中 `provider_type` 的值加载对应插件；同名时用户插件覆盖内置插件。`plugin.yaml` 示例（保留兼容）：

```yaml
name: honcho
version: 1.0.0
description: Honcho dialectic memory provider
hooks:
  - prefetch
  - sync_turn
  - get_tool_schemas
dependencies:
  - honcho-ai
```

### Provider 接口（`lib/memory/provider.py`）

Hermes 的 `MemoryProvider` 分为**核心生命周期方法**（必须实现）和**可选钩子**（重写启用）：

**核心方法**：

```python
class MemoryProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def is_available(self) -> bool: ...
    @abstractmethod
    async def initialize(self, session_id: str, **kwargs) -> None: ...
    async def system_prompt_block(self) -> str: ...
    async def prefetch(self, query: str, *, session_id: str = "") -> str: ...
    async def queue_prefetch(self, query: str, *, session_id: str = "") -> None: ...
    async def sync_turn(self, user: str, assistant: str, *, session_id: str = "") -> None: ...
    @abstractmethod
    async def get_tool_schemas(self) -> list[dict]: ...
    async def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str: ...
    async def shutdown(self) -> None: ...
```

**可选钩子**：

```python
    async def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None: ...
    async def on_session_end(self, messages: list[dict]) -> None: ...
    async def on_session_switch(self, new_session_id: str, *, reset: bool = False, **kwargs) -> None: ...
    async def on_pre_compress(self, messages: list[dict]) -> str: ...
    async def on_memory_write(self, action: str, target: str, content: str, metadata: dict | None = None) -> None: ...
    async def on_delegation(self, task: str, result: str, **kwargs) -> None: ...
    async def get_config_schema(self) -> list[dict]: ...
    async def save_config(self, values: dict, lumen_home: str) -> None: ...
```

**关键设计点**：

1. **内置 Provider 永远存在**：名为 `"builtin"`，直接操作 `memory.md` / `about_you.md`。不依赖外部服务。
2. **多外部 Provider 并存**：`MemoryManager.add_provider(provider, instance_name=...)` 使用配置实例名作为 key（如 `honcho-prod`、`honcho-dev`），支持同类型多实例；`prefetch_all()` / `sync_all()` 均 fan-out 到所有外部 provider。
3. **Provider 自带工具 schema**：外部 provider 通过 `get_tool_schemas()` 和 `handle_tool_call()` 暴露自己的工具（如 Honcho 的 `honcho_search`、Mem0 的 `mem0_profile`），Manager 负责汇总和路由。
4. **错误隔离**：所有 provider 调用包在 `try/except`。Prefetch 失败记 DEBUG（非致命），sync 失败记 WARNING，工具调用失败返回 `tool_error` JSON，shutdown 失败不阻塞退出。
5. **Metadata-aware 写入镜像**：内置 memory tool 写入时，`MemoryManager.on_memory_write()` 将 action/target/content/metadata 转发给所有外部 provider（跳过 builtin 自身），并通过签名反射兼容不同参数风格的 provider。
6. **主从关系（builtin 为主，external 为从）**：
   - `memory.md` / `about_you.md` 的唯一写入者是 builtin provider（通过 `AsyncMarkdownStore`）。
   - 外部 provider **绝不**直接写文件。它通过 `sync_turn()` 和 `on_memory_write()` 被动接收对话轮次或工具写入事件，自行决定是否同步到自己的后端（Honcho/Mem0）。
   - 这样保证：即使外部 provider 配置错误、离线或行为异常，builtin 的文件记忆仍然是完整、可靠的 truth source。
7. **Prefetch fan-out 隔离**：`prefetch_all()` 同时向所有 provider 发起预取，结果按 `[provider.name]` 标注后拼接。一个 provider 失败只跳过它，其他 provider 的结果不受影响。

### MemoryManager 职责（`lib/memory/manager.py`）

Hermes 的 `MemoryManager` 是一个**fan-out 编排器**：

```python
class MemoryManager:
    def add_provider(self, provider: MemoryProvider) -> None: ...
    async def build_system_prompt(self) -> str: ...
    # ↑ 汇总各 provider 的 system_prompt_block()，在会话启动时取一次、冻结进 system prompt。
    #   其中 builtin 返回 about_you.md（+ memory.md）的冻结快照（= L0）；外部 provider 返回自我介绍。
    #   mid-session 不更新，保护 prefix cache。
    async def build_context(self, user_id: str, user_input: str, *, session_key: str = "", conversation_summary: str = "") -> str: ...
    # ↑ 每轮动态上下文：只含外部 provider 的 prefetch 召回（L2）+ 当前时间，以 <memory-context> 围栏注入。
    #   不再包含 about_you.md（L0 已在上面的冻结 system prompt 里）；L1 近期对话本就在消息历史中。
    #   session_key（channel+chat_id）传给外部 provider 的 prefetch，做多渠道隔离。
    async def prefetch_all(self, query: str, *, session_id: str = "") -> str: ...
    async def queue_prefetch_all(self, query: str, *, session_id: str = "") -> None: ...
    async def sync_all(self, user_msg, assistant_msg, *, session_id: str = "") -> None: ...
    async def get_all_tool_schemas(self) -> list[dict]: ...
    def has_tool(self, tool_name: str) -> bool: ...
    async def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str: ...
    async def on_turn_start(self, turn_number, message, **kwargs) -> None: ...
    async def on_pre_compress(self, messages) -> str: ...
    async def on_session_end(self, messages) -> None: ...
    async def on_memory_write(self, action, target, content, metadata=None) -> None: ...
    async def shutdown_all(self) -> None: ...
    async def initialize_all(self, session_id, **kwargs) -> None: ...
```

**上下文围栏（Context Fencing）**：

Hermes 实现了三层防护防止记忆上下文被模型误认为是用户指令：

1. **静态围栏**：`build_memory_context_block()` 将 prefetch 结果包在：
   ```
   <memory-context>
   [System note: The following is recalled memory context, NOT new user input.
   Treat as authoritative reference data — this is the agent's persistent memory
   and should inform all responses.]
   <recalled content>
   </memory-context>
   ```
2. **清洗函数**：`sanitize_context()` 在包装前去除 provider 可能预带的围栏标签，防止双重包装。
3. **流式清洗器**：`StreamingContextScrubber` 用状态机跨 SSE chunk 清洗 `<memory-context>` 标签。因为块边界可能割裂标签，简单正则无法工作——必须用状态机跟踪是否在 span 内，并暂存可能不完整的标签尾部。

Lumen 把 `build_memory_context_block()`、`sanitize_context()`、`StreamingContextScrubber` 都放在 `lib/memory/context_fence.py`（见 §1.6）；`lib/channels/web.py`（SSE 输出）从该模块实例化 `StreamingContextScrubber`。

### 内置 Provider（`lib/memory/builtin_provider.py`）

内置 provider 对应 Hermes 的 `MemoryStore`，是一个**有界、文件-backed 的记忆系统**：

```python
class BuiltinMemoryProvider(MemoryProvider):
    @property
    def name(self) -> str:
        return "builtin"

    async def system_prompt_block(self) -> str:
        # 进入一段对话时取一次 about_you.md（+ memory.md）的冻结快照，注入该对话的 system prompt。
        # 这是 builtin 唯一的上下文注入点（对应 Hermes 的 format_for_system_prompt）。
        # 快照按 conversation（chat_id）冻结，详见上文「记忆作用域与会话语义」。
        ...

    async def prefetch(self, query: str) -> str:
        # 返回 ""：builtin 的记忆已在 system prompt 的冻结快照里，不再每轮重复注入。
        # 显式检索由 memory_search 工具完成（直接对 memory.md 做文本匹配）。
        return ""

    async def sync_turn(self, user: str, assistant: str) -> None:
        # 内置文件记忆不自动保存每轮对话；空操作
        pass

    async def get_tool_schemas(self) -> list[dict]:
        # 不暴露额外工具；Lumen 核心记忆工具直接操作 markdown
        return []
```

**关键实现细节（复刻 Hermes MemoryStore）**：

1. **条目格式**：用 Markdown bullet 追加到 `## Long-term notes` 章节下，每条带日期和 category（与设计文档一致）。例如：
   ```markdown
   ## Long-term notes

   - 2026-05-25 — [preferences] 用户偏好直接、面向实现的回答。
   - 2026-05-25 — [work] 用户在写一个 AI 伙伴项目，技术栈是 FastAPI + React。
   ```

2. **大小限制**：`memory.md` 默认 2,200 字符，`about_you.md` 默认 1,375 字符。超限时按策略丢弃旧条目。

3. **Frozen Snapshot（按 conversation 冻结）**：进入一段对话时取一次 `about_you.md`（+ `memory.md`）快照，组装进该对话的 system prompt，**该对话内多轮复用同一份**（system prompt 跨轮字节不变 → 命中 prefix cache）。mid-conversation 的 `memory_save` / 后台 review 写盘（持久）但不改这段对话的 prompt，新内容下一段对话才生效。实现上快照随会话状态按 `chat_id` 缓存。详见上文「记忆作用域与会话语义」。

4. **安全扫描**：所有写入内容过 `_scan_memory_content()`，检测：
   - Prompt injection 模式（"ignore previous instructions"、角色劫持）
   - 数据外泄模式（`curl` 带凭证、读取 `.env`）
   - 隐形 Unicode（零宽空格、双向覆盖字符）
   命中则拒绝写入并返回错误。

5. **并发安全**：用文件锁（`fcntl` Unix / `msvcrt` Windows）通过独立的 `.lock` 文件。每次修改：获取锁 → 重读磁盘 → 执行变更 → 原子写回。

### 插件加载器（`lib/memory/loader.py`）

```python
def discover_providers(plugins_dir: Path) -> dict[str, type[MemoryProvider]]:
    """扫描 ~/.lumen/plugins/memory/<name>/，读取 plugin.yaml，import __init__.py。"""
    ...

def load_provider(name: str, plugins_dir: Path) -> MemoryProvider:
    """按 name 加载并实例化 provider。"""
    ...
```

第一版实现：扫描目录 → 读 `plugin.yaml` → import `__init__.py` → 取 `Provider` 类 → 实例化。不需要处理 pip 依赖安装。

### NoOp Provider

当 `memory.provider` 未配置或为空时，使用 `NoOpMemoryProvider`：

```python
class NoOpMemoryProvider(MemoryProvider):
    @property
    def name(self) -> str:
        return "noop"

    async def is_available(self) -> bool:
        return True

    async def initialize(self, session_id: str, **kwargs) -> None:
        pass

    async def prefetch(self, query: str, *, session_id: str = "") -> str:
        return ""

    async def get_tool_schemas(self) -> list[dict]:
        return []

    async def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        return ""
```

---

## 阶段 1：建立新 Seam（Provider + Manager + Markdown 原子读写）

**目标**：在不碰任何现有 `GrowthEvent` 代码的前提下，新建 `lib/memory/` 下的完整文件优先基础设施，包括上述记忆插件子系统的全部组件。

### 1.1 新增 `lib/memory/provider.py`

- 定义抽象基类 `MemoryProvider`，接口与 Hermes 对齐（详见上文「记忆插件子系统 › Provider 接口」章节，以那里的 async 签名为准）。
- 除 `name`（property）外，全部为 `async` 方法。必须实现：
  - `name: str`（property）
  - `is_available() -> bool`
  - `initialize(session_id, **kwargs)`
  - `system_prompt_block() -> str`
  - `prefetch(query: str) -> str`
  - `queue_prefetch(query: str)`
  - `sync_turn(user: str, assistant: str)`
  - `get_tool_schemas() -> list[dict]`
  - `handle_tool_call(tool_name: str, args: dict) -> str`
  - `on_pre_compress(messages: list) -> str`
  - `on_session_end(messages: list)`
  - `shutdown()`
  - `get_config_schema() -> list[dict]`
  - `save_config(values: dict, lumen_home: str)`
- 提供默认空实现 `NoOpMemoryProvider`。

### 1.2 新增 `lib/memory/manager.py`

- `MemoryManager` 为进程级单例（或显式实例注入）。
- 职责（详见上文「记忆插件子系统」章节）：
  - 维护 provider 列表：内置 `"builtin"` 始终存在，外部 provider 最多一个。
  - `add_provider(provider)`：注册 provider，拒绝第二个外部 provider。
  - `build_system_prompt()`：汇总各 provider 的 `system_prompt_block()`，**会话启动时取一次、冻结进 system prompt**。builtin 返回 `about_you.md`（+ `memory.md`）的冻结快照（= L0）；外部 provider 返回静态自我介绍。mid-session 不更新，保护 prefix cache。
  - `prefetch_all(query, *, session_key="")`：每轮向所有 provider 发起 `prefetch()`，`session_key` 透传做多渠道隔离；结果按 `[provider.name]` 标注后拼接，用 `build_memory_context_block()` 包围栏。builtin 的 `prefetch()` 返回 `""`（其内容已在冻结快照里），故此处实际只汇集外部 provider 的动态召回（= L2）。任一 provider 失败只跳过它，不影响其他。
  - `queue_prefetch_all(query, *, session_key="")`：为下一回合排队后台预取。
  - `sync_all(user_msg, assistant_msg, *, session_key="")`：将对话轮次广播到所有 provider（`session_key` 透传）。builtin 的 `sync_turn()` 是**空操作**（`memory.md` 不每轮自动增长，只有显式 `memory_save` / `tell` / 后台 review 才写入）；外部 provider 通过 `sync_turn()` 被动接收，自行决定是否同步到后端。
  - `get_all_tool_schemas()` / `has_tool()` / `handle_tool_call()`：工具路由。
  - `on_turn_start()` / `on_pre_compress()` / `on_session_end()`：生命周期钩子转发。
  - `shutdown_all()` / `initialize_all()`：批量初始化和关闭。
- **错误隔离**：所有 provider 调用必须包在 `try/except` 中，失败只记录 warning，不抛异常打断聊天。

### 1.3 新增 `lib/memory/markdown.py`（改造后）

- 移除与 `GrowthEvent` 投影相关的代码，保留文件读写逻辑。
- 新增 `AsyncMarkdownStore`：
  - `read_memory(user_id) -> str` / `write_memory(user_id, content: str)`
  - `read_about_you(user_id) -> str` / `write_about_you(user_id, content: str)`
  - **原子写入**：用 `aiofiles` 写临时文件再 rename。
  - **并发锁**：每个用户一个 `asyncio.Lock`，串行化并发写入。这是**关键路径**而非防御性代码——同一 `user_id` 可能 web + telegram + 后台 review 在同一进程内并发写 `memory.md`。
  - **文件锁**：跨进程锁通过 `.lock` 文件（`fcntl` Unix / `msvcrt` Windows），防止多 Lumen 实例冲突。
- **安全扫描**：`_scan_memory_content(content) -> bool`，检测 prompt injection、数据外泄、隐形 Unicode，命中则拒绝写入。
- **条目格式**：用 Markdown bullet 追加到 `## Long-term notes` 章节下，每条带日期和 category。例如：
  ```markdown
  ## Long-term notes

  - 2026-05-25 — [preferences] 用户偏好直接、面向实现的回答。
  ```
- **大小限制**：`memory.md` 默认 2,200 字符，`about_you.md` 默认 1,375 字符，超限时丢弃最旧条目。
- **Frozen Snapshot 底层支持**：提供 `load_frozen_snapshot(user_id) -> str` 方法，供 builtin provider 在进入对话时读取一次快照。store 本身只按 `user_id` 读文件；**按 conversation（`chat_id`）冻结/缓存的逻辑在会话状态层**（builtin provider / agent_runner），不在 store。mid-conversation 写盘不影响已缓存的快照（保护 prefix cache）。
- 路径：`~/.lumen/memory/{user_id}/memory.md` 和 `about_you.md`。

### 1.4 新增 `lib/memory/builtin_provider.py`

- 实现 `BuiltinMemoryProvider(MemoryProvider)`。
- `name = "builtin"`。
- `prefetch()`：读取 `memory.md`，按段落做简单文本匹配，返回相关段落。
- `sync_turn()`：空操作（内置文件记忆不自动保存每轮对话）。
- `get_tool_schemas()`：返回空列表（Lumen 核心记忆工具直接操作 markdown，不通过 provider 工具路由）。
- 内置 provider 始终被 `MemoryManager` 自动注册，用户无需配置。

### 1.5 新增 `lib/memory/loader.py`

- 实现插件发现机制（详见上文「记忆插件子系统」章节）。
- `discover_providers(plugins_dir: Path) -> dict[str, type[MemoryProvider]]`：扫描 `~/.lumen/plugins/memory/<name>/`，import `__init__.py`，返回 name -> ProviderClass 映射。
- `load_provider(name: str, plugins_dir: Path) -> MemoryProvider`：实例化指定 provider。
- 第一版实现可以简单：扫描目录 → import → 取 `Provider` 类 → 实例化。不需要 pip 依赖管理。
- 若 `memory.provider` 未配置或为空，不加载外部 provider，只保留 builtin。

### 1.6 新增 `lib/memory/context_fence.py`

- 实现 `build_memory_context_block(raw_context: str) -> str`：将 prefetch 结果包在 `<memory-context>` 标签中，附系统说明。
- 实现 `sanitize_context(text: str) -> str`：去除 provider 可能预带的围栏标签，防止双重包装。
- 实现 `StreamingContextScrubber`：状态机跨 SSE chunk 清洗 `<memory-context>` 标签。必须处理块边界割裂标签的情况。
  - `feed(text: str) -> str`：清洗后返回用户可见文本。
  - `flush() -> str`：流结束时输出暂存内容。
  - `reset()`：新回合开始时重置状态。
- WebChannel（`lib/channels/web.py`）在 SSE 流式输出前实例化 scrubber，每个 delta 经过 `feed()`，流结束调用 `flush()`。

### 1.7 改造 `lib/memory/__init__.py`

- 公开导出：
  - `MemoryProvider`, `NoOpMemoryProvider`, `BuiltinMemoryProvider`
  - `MemoryManager`
  - `AsyncMarkdownStore`
  - `discover_providers`, `load_provider`
- 不要导出任何 `GrowthEvent` 相关符号。

### 验收标准

- [ ] `pytest tests/test_memory_*.py`（新建测试）通过：provider 接口、manager 组装上下文、markdown 原子读写。
- [ ] `python -c "from lib.memory import MemoryManager; m = MemoryManager(); print(m.build_context('demo_user', '', ''))"` 不抛异常。
- [ ] 本阶段结束后，`GrowthEvent` 代码**未被修改或引用**。

---

## 阶段 2：改造 Agent 工具层

**目标**：让 `memory_save`、`memory_search`、`get_profile`、`update_profile` 走新的 manager/markdown 路径。

### 2.1 改造 `lib/tools/memory.py`

- `memory_save(content: str, category: str)`：
  - 直接调用 `AsyncMarkdownStore.write_memory()`，将内容追加到 `memory.md`。
  - 第一版格式：用 Markdown bullet 追加到 `## Long-term notes` 章节下，每条带日期和 category。例如：
    ```markdown
    ## Long-term notes

    - 2026-05-25 — [preferences] 用户偏好直接、面向实现的回答。
    ```
  - 写入成功后，触发 `about_you.md` 刷新（复用现有 understanding 逻辑，或安排后台任务）。
- `memory_search(query: str)`：这是**显式检索工具**，与每轮自动注入无关（builtin 内容已在 system prompt 冻结快照里，`builtin.prefetch` 返回 `""`，不参与此处）。
  - 直接对 `memory.md` 做简单文本匹配（读全文，按段落/行过滤关键词）；
  - 若配置了外部 provider，再附加它的 `prefetch(query)` 召回结果；
  - 返回字符串，**不返回事件 ID**。

### 2.2 改造 `lib/tools/profile.py`

- `get_profile()`：
  - 读取 `about_you.md`，若不存在则回退到 `memory.md`。
- `update_profile(content: str)`：
  - 将内容直接追加/合并到 `memory.md`，不创建事件。
  - 触发 `about_you.md` 刷新（同 `memory_save`）。

### 2.3 工具 schema 调整

- `memory_save` 的 schema 中移除 `event_id`、`confirmation_status` 等字段。
- `memory_search` 的 schema 中移除 `limit`、`offset` 等分页字段（第一版不需要）。

### 2.4 注册

- 在 `lib/tools/factory.py` 中确保新工具被注册到 `ToolRegistry`。
- 如果旧工具和新工具文件名冲突，先保留旧文件（重命名加 `_old`），新文件用原文件名，避免前端/路由引用断裂。

### 验收标准

- [ ] `memory_save("test content", "preferences")` 后 `memory.md` 出现对应文本。
- [ ] `memory_search("preferences")` 能返回包含 "test content" 的结果。
- [ ] `get_profile()` 返回 `about_you.md` 或 `memory.md` 内容。
- [ ] `update_profile("new fact")` 后 `memory.md` 出现 "new fact"。
- [ ] 工具测试通过。

---

## 阶段 3：改造 API 路由 + 前端记忆 UI

**目标**：后端路由和前端页面从"事件列表/逐条管理"改为"全文编辑"。

### 3.1 后端路由改造 `server/routes/memory.py`

#### 保留并改造

| 路由 | 改造方式 |
|---|---|
| `GET /api/memory/me` | 读 `memory.md` 全文返回。 |
| `PUT /api/memory/me` | 接收完整 Markdown，原子写入 `memory.md`。 |
| `POST /api/memory/reset` | 清空 `memory.md` 和 `about_you.md`（写空字符串）。 |
| `GET /api/memory/understanding` | 读 `about_you.md` 返回；文件不存在返回空对象。 |
| `POST /api/memory/understanding/refresh` | 调用 `understanding.py` 从 `memory.md` 重新生成 `about_you.md`。 |
| `POST /api/memory/understanding/correct` | 直接覆写 `about_you.md`。 |
| `POST /api/memory/tell` | 将用户文本追加到 `memory.md`。 |
| `GET /api/memory/search` | 调用新 `memory_search` 工具逻辑，返回字符串结果。 |

#### 删除或退役

| 路由 | 处理方式 |
|---|---|
| `GET /api/memory/list` | 返回空列表 `[]`（前端兼容），后续阶段彻底删除。 |
| `DELETE /api/memory/{event_id}` | 返回 404 或空对象，不再实现。 |
| `PATCH /api/memory/{event_id}` | 返回 404 或空对象，不再实现。 |
| `POST /api/memory/{event_id}/review` | 返回 404 或空对象，不再实现。 |
| `GET /api/memory/observations` | 返回空列表 `[]`，不再实现。 |

### 3.2 前端 `src/pages/Memories.tsx` 改造

- 移除逐条事件列表渲染。
- 改为：
  - 加载时 `GET /api/memory/me`，显示在 `<textarea>` 或富文本编辑器中。
  - "保存" 按钮调用 `PUT /api/memory/me`。
  - "重置记忆" 按钮调用 `POST /api/memory/reset`。
  - "刷新 AI 理解" 按钮调用 `POST /api/memory/understanding/refresh`。
- 如果当前使用 `src/lib/api/memory.ts` 中的函数，更新对应 API 调用。

### 3.3 前端 `src/pages/Settings.tsx` 记忆区域改造

- 删除事件审核、逐条编辑、逐条删除的 UI 控件。
- 改为：
  - 显示当前记忆文件路径提示（可选）。
  - 一个链接跳转到 Memories 页面进行全文编辑。

### 3.4 前端 `src/pages/Profile.tsx` 改造

- 保留：
  - "关于你" 展示（读 `about_you.md`）。
  - "纠正关于你"（覆写 `about_you.md`）。
  - "告诉 Lumen" 表单（追加到 `memory.md`）。
  - "刷新理解"。
  - "重置记忆"。
- **第一阶段隐藏或移除**（不硬造数据）：
  - `你的心愿` / intents；
  - `此刻` / now status；
  - `你走过的路` / journey。
- 这些组件的条件渲染：数据为空时不显示，或整体注释掉。

### 3.5 前端 `src/components/ObservationStrip.tsx`

- 删除或注释掉。旧接口依赖 `GrowthEvent`，新架构下无对应数据。

### 3.6 Notes 处理

- **直接删除 notes 功能**。
- 删除 `server/routes/notes.py` 及对应前端组件。
- 删除 `lib/tools/notes.py` 或相关工具注册。
- 不需要新建 `lib/notes/` 模块。

### 验收标准

- [ ] 前端 Memories 页面能加载、编辑、保存 `memory.md` 全文。
- [ ] Profile 页面能正常展示"关于你"，隐藏 journey/intents/now-status。
- [ ] Settings 不再调用事件删除/审核接口。
- [ ] 后端路由测试通过（新建测试覆盖改造后的路由）。

---

## 阶段 4：改造 Understanding / Snapshot / Context 注入

**目标**：让 Agent 的上下文组装走新 manager，不再引用 `GrowthEvent`。

### 4.1 改造 `lib/memory/understanding.py`

- 输入从 `memory.md` 读取，而不是从 `GrowthEvent` 查询。
- 输出写入 `about_you.md`。
- 保留现有 LLM 生成逻辑（prompt、模型调用），只改数据来源和写入目标。
- 保留 5 分钟防抖逻辑（`_DEBOUNCE_SECONDS = 300`）。

### 4.2 改造 `lib/memory/snapshot.py`

`snapshot.py` 的角色改为**薄封装层**：
- 内部逻辑全部委托给 `MemoryManager.build_context()`，不再自己组装 L0/L1/L2。
- 保留 `build_context()` 函数签名作为兼容层，但实现改为 `await manager.build_context(user_id, user_input)`。
- **本计划保留** `snapshot.py` 作为薄兼容层，不在本计划内删除。若后续确认无其他调用方，可在独立的清理计划中移除，让 `agent_runner.py` 直接调用 `memory_manager.build_context()`。
- 移除与 `GrowthEvent` 相关的任何查询或过滤逻辑。

### 4.3 改造 `lib/chat/agent_runner.py`

作用域约定见上文「记忆作用域与会话语义」：文件按 `user_id`，召回/会话生命周期按 `session_key`（`channel` + `chat_id`），L0 冻结快照按 **conversation（`chat_id`）**。AgentRunner 处理的**任何 run**（用户触发或 presence 驱动的主动推送）都走下述注入路径。

- **系统提示词（进入对话时组装一次，该对话全程冻结）**：
  1. 基础 system prompt（角色定义、工具说明）
  2. `memory_manager.build_system_prompt()` —— 包含 builtin 的 `about_you.md`（+ `memory.md`）**冻结快照（L0）** + 外部 provider 自我介绍。
  - 快照随会话状态按 `chat_id` 缓存，该对话内多轮**喂同一份 system prompt**（字节不变 → 命中 prefix cache）；mid-conversation 的 `memory_save` / 后台 review 写盘但不改这段对话的 prompt，新内容**下一段对话**才生效。
- **每轮动态上下文**：turn 开始前调用 `memory_manager.build_context(user_id, user_input, session_key=session_key)`，只含外部 provider 的 `prefetch`（**L2**，按 `session_key` 隔离）+ 当前时间，已包成 `<memory-context>` 围栏，追加在消息序列里（不进冻结的 system prompt）。无外部 provider 时为空。
  - L1（近期对话）本就在消息历史中，不需要额外注入。
  - `session_key` 从 bus 事件（`TurnStarted.channel` + `chat_id`）取，透传给 `prefetch_all` / `sync_all` / `on_session_switch`，做多渠道隔离。
- 在 assistant turn 结束后调用 `memory_manager.sync_all(user_msg, assistant_msg, session_key=session_key)`。
- 在对话摘要/压缩前调用 `memory_manager.on_pre_compress(messages)`。

### 4.4 改造 `lib/chat/session.py` 或 summary 服务

- 确认对话压缩/摘要流程中，如果需要记忆上下文，调用 `memory_manager.on_pre_compress()`。
- 移除任何直接 import `GrowthEvent` 的代码。

### 4.5 改造 `lib/memory/review_service.py`

- **保留**后台对话审查服务（设计文档第 236 行明确希望保留"后台 review"作为一条保存路径）。它的语义不变：当 Agent 在对话中未主动保存记忆时，后台 fork 一个 Agent 审查本轮对话，判断是否值得保存。
- 审查 fork 的 Agent 仍通过 `memory_save` / `update_profile` 工具写入——这些工具在阶段 2 已改为直接写 `memory.md`，因此 review_service 本身无需关心存储细节。
- 只需移除它对旧 facade / projection / 事件投影的依赖（包括 docstring 里"触发 .md 投影 + 缓存失效"的旧描述），确保它不再 import 任何 `GrowthEvent` 相关模块。
- 注意：本路径仍属于"有意图的保存"，不违反"`memory.md` 不会每轮自动增长"——只有审查判断有价值时才调用 `memory_save`。

### 验收标准

- [ ] Agent 启动后能正确读取 `about_you.md` 作为上下文。
- [ ] 发送消息后，`memory.md` 不会自动增长（只有显式 `memory_save` / `tell` / 后台 review 判定后才写入）。
- [ ] **同一对话内多轮的 system prompt 字节一致**（L0 按 `chat_id` 冻结）；mid-conversation 调 `memory_save` 后该对话 prompt 不变，新开对话才反映新内容。
- [ ] `session_key` 从 bus 透传到 `build_context` / `prefetch_all` / `sync_all`；web 与 telegram 同一 user 的外部 provider 召回互不串台。
- [ ] presence 驱动的主动 run 也能取到 L0。
- [ ] `snapshot.py` 不再 import `GrowthEvent`。
- [ ] `agent_runner.py` 不再 import `GrowthEvent`。
- [ ] `review_service.py` 不再 import `GrowthEvent` / facade / projection，后台审查通过工具写入 `memory.md`。

---

## 阶段 5：删除 `GrowthEvent` 及关联代码

**目标**：从运行时代码中彻底移除 `GrowthEvent`。

### 5.1 删除/清理 `lib/memory/` 下的旧模块

以下模块在确认新代码已完全替代其功能后删除：

- `lib/memory/models.py`（`GrowthEvent` 模型）
- `lib/memory/facade.py`（`LumenMemory`）
- `lib/memory/writer.py`
- `lib/memory/classifier.py`
- `lib/memory/events_merger.py`
- `lib/memory/relational_store.py`
- `lib/memory/projection.py`
- `lib/memory/searcher.py`
- `lib/memory/search.py`
- `lib/memory/observations.py`

**注意**：先注释掉 import 和调用，验证系统能启动后，再物理删除文件。

### 5.2 清理 `core/migrations.py`

- 移除 `growth_events` 表创建逻辑。
- 移除 `growth_events_fts` FTS5 虚拟表及触发器创建逻辑。
- 保留其他表（`users`, `conversations`, `messages` 等）的迁移。

### 5.3 清理 `lib/model_registry.py`

- 移除 `GrowthEvent` 的模型注册。

### 5.4 清理 `core/vector_store.py`

- 如果 `DocumentIndexProvider` 当前只为 `GrowthEvent` 服务，且未来无其他用途，可一并移除。
- 如果保留作为可插拔接口，确保它不再被 `GrowthEvent` 代码引用。

### 5.5 清理测试

- 删除所有依赖 `GrowthEvent` 的旧测试文件。
- 更新 `pyproject.toml` / `pytest` 配置中的测试路径（如有需要）。

### 5.6 清理前端类型和 API 客户端

- `src/lib/api/memory.ts` 中移除事件列表、事件审核、逐条删除相关的函数。
- `src/lib/api.ts` 中更新导出。

### 验收标准

- [ ] `grep -r "GrowthEvent" --include="*.py" .` 返回空（排除旧备份文件）。PowerShell：`rg "GrowthEvent" -g "*.py"` 或 `Get-ChildItem -Recurse -Include *.py | Select-String "GrowthEvent"`。
- [ ] `grep -r "from lib.memory" --include="*.py" .` 只引用新模块（provider, manager, markdown, understanding, snapshot, review_service）。
- [ ] 后端干净启动，不创建 `growth_events` 表。
- [ ] 所有保留的 API 路由正常工作。

---

## 阶段 6：全局验证与修复

**目标**：确保改造后系统完整、干净、可运行。

### 6.1 静态检查

> 本项目在 Windows / PowerShell 上开发；下方 bash 命令仅作参考，路径与删除命令请用 PowerShell 等价写法（见各处注释）。

```bash
# Python：确认无 GrowthEvent 引用（bash）
grep -r "GrowthEvent" --include="*.py" lib/ core/ server/ tests/
# PowerShell 等价：
#   Get-ChildItem -Recurse -Include *.py lib,core,server,tests | Select-String "GrowthEvent"
# 或（若已装 ripgrep，跨平台一致）：
#   rg "GrowthEvent" -g "*.py" lib core server tests

# Python：确认新模块可正常 import
python -c "from lib.memory import MemoryManager, AsyncMarkdownStore; print('OK')"

# TypeScript：前端类型检查
npm run typecheck
# 或
npx tsc --noEmit
```

### 6.2 测试

```bash
# 后端测试
pytest

# 如果测试数量大幅减少（因为删了很多旧测试），确认核心流程测试覆盖：
pytest tests/test_memory_markdown.py      # 新建
pytest tests/test_memory_manager.py       # 新建
pytest tests/test_memory_provider.py      # 新建
pytest tests/test_chat_agent_runner.py    # 改造后
pytest tests/test_api_memory.py           # 改造后
```

### 6.3 启动验证

```bash
# 干净数据库启动
rm ~/.lumen/lumen.db
# PowerShell 等价：Remove-Item $env:USERPROFILE\.lumen\lumen.db
python -m uvicorn main:app --reload

# 检查健康
curl http://localhost:8000/api/health

# 检查记忆接口
curl http://localhost:8000/api/memory/me
```

### 6.4 端到端验证

- 打开前端 → 发送消息 → Agent 正常回复。
- 在 Memories 页面编辑记忆 → 保存 → 刷新页面 → 内容保留。
- 在 Profile 页面点击"刷新理解" → `about_you.md` 生成。

### 6.5 最终代码审查清单

- [ ] 没有双 truth source：`memory.md` 是唯一长期记忆来源，`about_you.md` 是生成的派生文件。
- [ ] 没有临时兼容层残留：退役 API 是否已彻底删除（不只是返回空）。
- [ ] 没有 `GrowthEvent` import。
- [ ] 没有 `fts5` 记忆相关触发器创建代码。
- [ ] Provider 失败被正确隔离，不会打断聊天。

---

## 附录 A：文件变更速查表

### 新增文件

| 文件 | 说明 |
|---|---|
| `lib/memory/provider.py` | MemoryProvider 抽象基类 + NoOpMemoryProvider |
| `lib/memory/manager.py` | MemoryManager fan-out 编排器 |
| `lib/memory/builtin_provider.py` | BuiltinMemoryProvider（文件-backed 有界存储） |
| `lib/memory/loader.py` | Provider 插件发现（扫描 plugin.yaml） |
| `lib/memory/markdown.py` | AsyncMarkdownStore（原子写入 + 安全扫描 + 文件锁） |
| `lib/memory/context_fence.py` | build_memory_context_block + StreamingContextScrubber |
| `tests/test_memory_*.py` | 新记忆模块测试 |

### 改造文件

| 文件 | 改造内容 |
|---|---|
| `lib/memory/__init__.py` | 导出新符号，移除旧符号 |
| `lib/memory/understanding.py` | 数据来源改为 `memory.md`，输出到 `about_you.md` |
| `lib/memory/snapshot.py` | 调用 MemoryManager，移除 GrowthEvent 查询（保留为薄兼容层） |
| `lib/memory/review_service.py` | 保留后台审查；移除 facade/projection/事件依赖，写入走工具 → `memory.md` |
| `lib/tools/memory.py` | 直接写 `memory.md`，搜索走 provider + 文本匹配 |
| `lib/tools/profile.py` | 直接读/写 `memory.md` / `about_you.md` |
| `lib/tools/factory.py` | 注册新工具，移除旧工具引用 |
| `lib/chat/agent_runner.py` | 调用 MemoryManager 组装上下文和同步轮次 |
| `lib/chat/session.py` | 如有记忆相关调用，改走 MemoryManager |
| `lib/channels/web.py` | SSE 流式输出接入 StreamingContextScrubber |
| `server/routes/memory.py` | 路由语义改为全文编辑 |
| `core/migrations.py` | 移除 growth_events 相关迁移 |
| `lib/model_registry.py` | 移除 GrowthEvent 注册 |
| `core/vector_store.py` | 移除或隔离 GrowthEvent 引用 |
| `src/pages/Memories.tsx` | 改为全文编辑器 |
| `src/pages/Settings.tsx` | 移除事件管理控件 |
| `src/pages/Profile.tsx` | 隐藏 journey/intents/now-status |
| `src/components/ObservationStrip.tsx` | 删除或注释 |
| `src/lib/api/memory.ts` | 更新 API 函数 |
| `src/lib/api.ts` | 更新导出 |

### 删除文件（最终阶段）

| 文件 | 说明 |
|---|---|
| `lib/memory/models.py` | GrowthEvent ORM |
| `lib/memory/facade.py` | LumenMemory |
| `lib/memory/writer.py` | 事件写入 |
| `lib/memory/classifier.py` | 事件分类 |
| `lib/memory/events_merger.py` | 事件合并 |
| `lib/memory/relational_store.py` | 关系存储 |
| `lib/memory/projection.py` | .md 投影 |
| `lib/memory/searcher.py` | 搜索组装 |
| `lib/memory/search.py` | FTS5 搜索 |
| `lib/memory/observations.py` | 观察事件 |
| `tests/test_memory_dedup.py` | 旧去重测试 |
| `tests/test_memory_writer.py` | 旧写入测试 |
| `tests/test_memory_search.py` | 旧搜索测试 |
| ...其他依赖 GrowthEvent 的测试文件 | |

---

## 附录 B：Kimi 执行提示

1. **按阶段顺序执行**，不要跨阶段并行修改。阶段 1 完成后才能进入阶段 2。
2. **每个阶段结束后运行验收标准中的检查命令**，不通过不进入下一阶段。
3. **删除操作放在最后**（阶段 6），先确保新代码完全替代旧功能。
4. **不要保留双 truth source**。任何"临时兼容"代码必须在阶段 6 前清理完毕。
5. **前端隐藏 vs 删除**：第一阶段先隐藏 journey/intents/now-status（条件渲染或注释），不要硬造数据。
6. **Provider 接口先空跑**：`NoOpMemoryProvider` 让系统在无外部 provider 时也能正常工作。
7. **Markdown 写入必须原子**：用临时文件 + rename，不要用直接覆盖。
8. **并发锁**：`AsyncMarkdownStore` 每个用户一个 `asyncio.Lock`，跨进程用文件锁（`.lock` 文件）。
9. **安全扫描**：`_scan_memory_content()` 必须检测 prompt injection、数据外泄、隐形 Unicode。
10. **Frozen Snapshot**：system prompt 只在会话启动时取一次快照，mid-session 写入不更新。
11. **StreamingContextScrubber**：必须用状态机，不能简单正则，处理跨 chunk 的标签边界。
12. **条目格式**：用 Markdown bullet（`- 日期 — [category] 内容`）追加到 `## Long-term notes` 章节下，与设计文档一致。
13. **如果遇到困难**：停下来问，不要猜。把具体错误和所在文件贴出来。

---

## 附录 C：风险与回滚

| 风险 | 缓解措施 |
|---|---|
| 新 Markdown 读写有并发 bug | 阶段 1 就加 per-user lock，阶段 6 做并发测试 |
| 旧测试大量删除导致覆盖不足 | 每个改造阶段同步写新测试，阶段 6 确认核心流程覆盖 |
| 前端类型断裂 | 阶段 3 同步跑 `npm run typecheck`，不通过不合并 |
| Provider 接口设计遗漏 | 第一版保持简单，后续迭代扩展；NoOp 兜底 |
| 用户突然要求保留旧数据 | 本计划明确不做迁移，如需求变更需另开计划 |

---

*本计划基于已批准的设计文档编写。如有实现细节需要调整，在不改变高层架构决策的前提下，Kimi 可自行判断；若涉及架构变更，需重新审阅设计文档。*
