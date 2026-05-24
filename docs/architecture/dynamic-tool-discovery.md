# 动态工具发现架构设计

> 状态：方案设计阶段  
> 背景：解决当前 Lumen 工具层全量喂给 Agent 导致的上下文膨胀、选择噪声和安全风险问题。  
> 参考：akashic-agent 的 ToolRegistry + ToolDiscoveryState + tool_search 元工具机制。

---

## 1. 问题诊断

### 1.1 当前架构

```
┌─────────────────────────────────────────┐
│ assemble_tools()                        │
│   ├─ create_file_tools()     → 4 个    │
│   ├─ create_memory_tools()   → 2 个    │
│   ├─ create_profile_tools()  → 2 个    │
│   ├─ create_notes_tools()    → 2 个    │
│   ├─ create_web_search_tools() → 1 个  │
│   ├─ create_shell_tools()    → 3 个    │
│   └─ _discover_mcp_tools()   → N 个    │
│                                          │
│ wrap_with_logging() + wrap_with_budget() │
│                                          │
│ build_pydantic_toolset() → 全量转换     │
│                                          │
│ Agent(toolsets=[全部工具])               │
└─────────────────────────────────────────┘
```

**现状**：`assemble_tools()` 把 14+ 个内置工具和所有 MCP 工具全部转换为 PydanticAI `FunctionToolset`，每次 Agent run 完整传递。

### 1.2 带来的问题

| 问题 | 影响 | 量化估算 |
|------|------|---------|
| **上下文膨胀** | 每个工具的 JSON Schema + description 占用 token | 14 个工具 ≈ 500-1200 token；接入 MCP 后 30+ 工具 ≈ 1500-3000 token |
| **选择噪声** | Agent 面对不相关工具容易误调用 | 闲聊时误触 `shell`、`file_write`；提问时误触 `file_read` |
| **延迟增加** | LLM 在更多选项中做 function calling 决策 | 工具数量每翻倍，推理延迟增加 10-20% |
| **安全风险** | 危险工具（`shell`、`file_write`）始终可见 | Agent 可能在用户无明确意图时执行破坏性操作 |
| **MCP 失控** | 接入 filesystem/github 等 MCP 后工具数暴涨 | 单 MCP server 可贡献 10-20 个工具，全量加载不可持续 |

### 1.3 目标

- **token 可控**：每轮对话可见工具数稳定在 5-10 个，不随 MCP 接入线性增长
- **按需加载**：Agent 只在需要时获取工具，不需要的工具不进入 LLM 上下文
- **会话记忆**：上轮解锁/使用过的工具，本轮自动预加载
- **安全隔离**：写操作和危险工具默认隐藏，需主动解锁

---

## 2. 核心设计

### 2.1 三层工具可见性

借鉴 akashic-agent 的分层模型：

```
┌─────────────────────────────────────────┐
│ Layer 1: always_on（常驻）               │
│   每轮都可见，核心对话能力                │
│   e.g. memory_search, memory_save,      │
│        get_profile, update_profile,     │
│        tool_search                      │
├─────────────────────────────────────────┤
│ Layer 2: preloaded（预加载）             │
│   本 conversation 最近使用/解锁过的工具   │
│   LRU 缓存，容量 10，自动淘汰             │
│   e.g. 上轮用过 web_search → 本轮直接可见 │
├─────────────────────────────────────────┤
│ Layer 3: deferred（延迟）                │
│   默认不可见，需通过 tool_search 解锁     │
│   目录注入 system prompt，Agent 知道存在  │
│   e.g. shell, file_write, web_search,   │
│        MCP tools...                     │
└─────────────────────────────────────────┘
```

### 2.2 关键组件

#### 2.2.1 ToolMeta（工具元数据）

为每个工具附加元数据，支撑分层决策：

```python
@dataclass
class ToolMeta:
    risk: str = "read-only"           # "read-only" | "write" | "destructive"
    always_on: bool = False           # 是否常驻
    search_hint: str | None = None    # 搜索别名/口语化表达
    tags: list[str] = field(default_factory=list)  # 分类标签
```

**风险等级定义**：
- `read-only`：纯读取，无副作用（`memory_search`, `file_read`, `web_search`）
- `write`：写入数据但无系统级风险（`memory_save`, `update_profile`, `file_write`）
- `destructive`：可能破坏系统或产生不可逆操作（`shell`, `task_stop`）

**always_on 策略（初始值）**：

| 工具 | always_on | risk | 理由 |
|------|-----------|------|------|
| memory_search | ✅ | read-only | 核心伙伴能力，随时可能需要回忆 |
| memory_save | ✅ | write | 核心伙伴能力：听到用户信息必须立即保存，若 deferred 会漏记 |
| get_profile | ✅ | read-only | 画像读取是基础上下文 |
| update_profile | ❌ | write | 用户主动更新画像的场景低频， deferred 更合理；需要时通过 tool_search 解锁 |
| tool_search | ✅ | read-only | 元工具本身必须是常驻的，否则 Agent 无法发现其他工具 |
| data_source_search | ❌ | read-only | 非高频，按需加载 |
| notes_list | ❌ | read-only | 非高频，按需加载 |
| web_search | ❌ | read-only | 仅在需要外部信息时加载 |
| file_read | ❌ | read-only | 仅在用户提及文件时加载 |
| file_write | ❌ | write | 写文件应谨慎，按需解锁 |
| file_ls | ❌ | read-only | 低频，按需加载 |
| file_grep | ❌ | read-only | 低频，按需加载 |
| shell | ❌ | destructive | 必须按需解锁，安全第一 |
| task_output | ❌ | read-only | shell 的配套工具，随 shell 一起解锁 |
| task_stop | ❌ | destructive | shell 的配套工具，随 shell 一起解锁 |
| MCP tools | ❌ | 按定义 | 全部 deferred，按 server 分组 |

#### 2.2.2 ToolRegistry（工具注册表）

全局单例，替代当前扁平的 `assemble_tools()` list：

```python
class ToolRegistry:
    """管理所有可用工具，提供搜索和过滤能力。"""

    def register(self, tool: ToolDef, meta: ToolMeta) -> None: ...
    def unregister(self, name: str) -> None: ...
    def get_schemas(self, names: set[str]) -> list[dict]: ...
    def search(self, query: str, top_k: int = 5, excluded: set[str] | None = None) -> list[dict]: ...
    def get_always_on_names(self) -> set[str]: ...
    def get_deferred_names(self, visible: set[str] | None = None) -> dict[str, list[str]]: ...
    def get_documents(self) -> list[ToolDocument]: ...
```

**搜索后端**：简单关键词匹配即可，无需向量检索：

```python
def search(query: str, top_k=5, excluded=None):
    query = query.lower()
    scores = []
    for name, doc in self._documents.items():
        if name in excluded:
            continue
        score = 0
        if query in name.lower():
            score += 10
        if query in doc.description.lower():
            score += 5
        if doc.search_hint and query in doc.search_hint.lower():
            score += 3
        for tag in doc.tags:
            if query in tag.lower():
                score += 2
        if score > 0:
            scores.append((score, doc))
    return sorted(scores, key=lambda x: -x[0])[:top_k]
```

#### 2.2.3 ToolDiscoveryState（会话级 LRU）

按 `conversation_id` 维护预加载缓存。使用 `OrderedDict` 实现 LRU 语义：

```python
from collections import OrderedDict

class ToolDiscoveryState:
    """管理每个 conversation 的工具可见性缓存。

    设计为无状态重置：进程重启后缓存清空，用户需重新解锁工具。
    这是可接受的，因为 always_on 覆盖了核心能力，且解锁只需一轮 tool_search。
    长期演进可考虑将缓存持久化到 conversations.metadata_json 字段。
    """

    def __init__(self, capacity: int = 10, max_conversations: int = 1000) -> None:
        self._capacity = capacity
        self._max_conversations = max_conversations
        # conversation_id -> OrderedDict{tool_name: None}
        self._cache: OrderedDict[str, OrderedDict[str, None]] = OrderedDict()

    def get_visible(self, conversation_id: str) -> list[str]:
        """返回当前 conversation 预加载的工具列表（按最近使用排序）。"""
        od = self._cache.get(conversation_id)
        return list(od.keys()) if od else []

    def update(self, conversation_id: str, tool_names: list[str], always_on: set[str]) -> None:
        """将使用过的工具加入缓存，去重并淘汰溢出项。"""
        # 确保 conversation 存在
        if conversation_id not in self._cache:
            # 超出 conversation 数量上限时淘汰最久未用的 conversation
            if len(self._cache) >= self._max_conversations:
                self._cache.popitem(last=False)
            self._cache[conversation_id] = OrderedDict()
        od = self._cache[conversation_id]

        # 移动到末尾（最近使用）
        self._cache.move_to_end(conversation_id)

        for name in tool_names:
            if name in always_on:
                continue
            if name in od:
                od.move_to_end(name)
            else:
                od[name] = None
        # 截断到容量上限
        while len(od) > self._capacity:
            od.popitem(last=False)

    def clear(self, conversation_id: str) -> None:
        self._cache.pop(conversation_id, None)
```

**容量设计**：
- `capacity=10`：单个 conversation 最多保留 10 个 preloaded 工具。理由同上。
- `max_conversations=1000`：全局最多保留 1000 个 conversation 的缓存，防止内存泄漏。超限后淘汰最久未更新的 conversation。

**持久化策略**：当前设计为进程内存中的无状态缓存。进程重启后清空，用户需重新解锁。这是可接受的，因为：
- always_on 工具（5 个）覆盖了 80% 的日常对话场景
- 非常用工具重新解锁只需一轮 `tool_search` 调用
- 长期演进可将缓存序列化到 `conversations.metadata_json` 字段，在对话加载时恢复

#### 2.2.4 tool_search（元工具）

`always_on=True` 的常驻工具，Agent 用它来发现其他工具：

```python
async def _tool_search(args: dict[str, Any], deps) -> str:
    query = args.get("query", "").strip()
    top_k = min(int(args.get("top_k", 5)), 10)
    allowed_risk = args.get("allowed_risk")

    registry = get_tool_registry()
    discovery = get_tool_discovery_state()

    # 当前已可见的工具 = always_on + preloaded
    always_on = registry.get_always_on_names()
    preloaded = set(discovery.get_visible(deps.conversation_id))
    excluded = always_on | preloaded | {"tool_search"}

    # select: 精确加载路径
    if query.lower().startswith("select:"):
        return _handle_select(query[7:], registry, excluded, allowed_risk)

    # 关键词搜索路径
    results = registry.search(query, top_k=top_k, excluded=excluded)
    if not results:
        return json.dumps({"matched": [], "unlocked": [], "tip": "未找到匹配工具"})

    unlocked = [r["name"] for r in results]
    # 立即解锁：加入当前 conversation 的预加载缓存
    discovery.update(deps.conversation_id, unlocked, always_on)

    return json.dumps({
        "matched": results,
        "unlocked": unlocked,
        "next_action": "unlocked 中的工具已加载，可直接调用，不要再次 tool_search",
    })
```

**两种查询语法**：
- `select:tool_name` — 精确加载单个/多个工具（逗号分隔）
- `关键词` — 模糊搜索，返回 top_k 个匹配工具

#### 2.2.4 allowed_risk 语义

`allowed_risk` 是**过滤条件**，不是解锁门槛：

- 语义：只返回 risk 等级在 `allowed_risk` 列表中的工具
- 示例：`allowed_risk=["read-only"]` 时不会返回 `shell`（destructive）和 `file_write`（write）
- 由 Agent 自主传入：LLM 根据当前任务判断需要哪种风险等级的工具。例如"帮我查资料"时传入 `["read-only"]`，"帮我改配置"时传入 `["read-only", "write"]`
- 不传时不过滤，返回所有匹配工具

#### 2.2.5 解锁时序与 PydanticAI 限制

**核心限制：PydanticAI 在一次 `Agent.run()` 调用中不支持动态修改 toolset。**

PydanticAI 的 `run_stream` 在调用开始时构建一个 internal agent graph，`graph_ctx.deps.tool_manager` 在 tool loop 的 `process_tool_calls` 中被引用，但没有任何机制允许在 graph 运行中间替换 tool manager 或增减 toolset。toolsets 在 graph 初始化时固定，贯穿整个 run 生命周期。

**因此：`tool_search` 解锁的工具只能在「下一次 Agent run」时生效，无法在当前 tool loop 的后续轮次中立即使用。**

**实际影响示例**：
```
用户：帮我读 data.csv
Agent（可见工具：always_on 5 个）
  → 调用 tool_search("file_read")
  → 返回：{unlocked: ["file_read"], next_action: "已加载，可直接调用"}
  → 但当前 run 的 tool loop 已结束，file_read 的 schema 不在本次 LLM 上下文中
  → Agent 无法在当前轮调用 file_read
```

**缓解策略（策略 A——推荐）**：

1. **接受单轮限制**：`tool_search` 解锁的工具从**下一轮对话**开始自动预加载
2. **Agent 引导用户**：本轮 Agent 回复中说明"我已准备好 file_read 工具，请继续"
3. **用户继续对话**：用户发送下一条消息（即使只是"好的"或"读吧"），新工具已在预加载缓存中，Agent 直接可见
4. **无需重复搜索**：下轮对话 `discovery.get_visible(conv_id)` 自动返回 `file_read`

**为什么不采用策略 B（系统内部重新 run）**：

策略 B 指系统在检测到 `tool_search` 解锁新工具后，不返回给用户，而是内部重新发起一次 `Agent.run()`（带上新工具集）。这会导致：
- 额外 LLM 调用，延迟 + token 成本翻倍
- 可能产生无限循环（Agent 再次搜索 → 再次解锁 → 再次重跑）
- 流式输出中断，用户体验差

策略 A 是成本和体验的最优平衡。akashic-agent 使用自研 reasoner 可在 tool loop 中间更新 schema，但 Lumen 基于 PydanticAI 必须接受这一限制。

### 2.3 对话流程改造

```
改造前：
用户消息 → Agent.run_stream_events(toolsets=[全量工具]) → 回复

改造后：
用户消息 ──┬──→ 计算 visible = always_on ∪ discovery.get_visible(conv_id)
           ├──→ 从 Registry 取 visible 的 schema
           ├──→ 构建 deferred 目录 hint，追加到 system prompt
           ├──→ Agent.run_stream_events(toolsets=[可见工具]) → 回复
           └──→ 本轮结束后：
                discovery.update(conv_id, tools_used + tools_unlocked, always_on)
```

**system prompt 追加内容示例**：

```markdown
---

## 可用但未加载的工具

以下工具当前不可直接调用，但你可以使用 `tool_search` 搜索并加载它们：

builtin:
  - file_read, file_write, file_ls, file_grep
  - web_search, web_fetch
  - shell, task_output, task_stop
  - data_source_search, notes_list

mcp:
  - filesystem: filesystem_read_file, filesystem_list_directory, ...
  - github: github_search_code, github_create_issue, ...

加载方式：tool_search(query="select:工具名") 或 tool_search(query="功能关键词")
```

---

## 3. 文件改动清单

### 3.1 新增文件

| 文件 | 说明 | 预估行数 |
|------|------|---------|
| `lib/tools/_registry.py` | `ToolRegistry` 单例 + `ToolDocument` + 关键词搜索 | ~200 |
| `lib/tools/_discovery.py` | `ToolDiscoveryState`（conversation 级 LRU） | ~80 |
| `lib/tools/_search_tool.py` | `tool_search` 元工具实现 | ~150 |

### 3.2 修改文件

| 文件 | 修改内容 |
|------|---------|
| `lib/tools/_base.py` | `ToolDef` 新增 `meta: ToolMeta` 字段；新建 `ToolMeta` dataclass |
| `lib/tools/factory.py` | ① 改为向 `ToolRegistry` 注册工具（带 meta）；② 新增 `assemble_visible_tools(conversation_id)` 返回可见工具；③ `_discover_mcp_tools()` 注册时标记 source_type="mcp" |
| `core/agent.py` | ① `LumenAgent.create()` 改为从 Registry 获取 schema；② `build_system_prompt()` 追加 deferred 工具目录；③ 需要 conversation_id 参与 schema 构建 |
| `lib/chat/service.py` | ① 每轮对话前从 discovery 取 visible；② 传给 Agent；③ 轮次结束后用 discovery.update() 更新缓存 |

---

## 4. 边界情况处理

### 4.1 新对话（冷启动）

- `ToolDiscoveryState` 中没有该 conversation_id 的缓存
- 只有 always_on 工具（5 个）可见
- deferred 目录完整展示

### 4.2 长时间对话（缓存填满）

- LRU 达到容量 10，最久未用的工具被淘汰
- 下次需要时 Agent 重新 `tool_search` 加载
- **无状态丢失风险**，只是多一轮搜索

### 4.3 MCP Server 动态增删

- MCP 工具注册到 Registry 时标记 `source_type="mcp"`, `source_name=server_name`
- `_discover_mcp_tools()` 在 `assemble_tools()` 时调用，即 **Agent 重建时**一次性发现
- 注销 MCP server 时调用 `registry.unregister()` 移除其所有工具
- deferred 目录自动反映最新状态

**中途连接新 MCP server 的处理**：
- 当前 Lumen 已有 `_tool_fingerprint` 机制：MCP 工具列表变化时触发 Agent 重建
- 新 MCP server 连接后，其工具在下次 Agent 重建时进入 Registry 的 deferred 目录
- 用户可通过 `tool_search` 搜索到新 MCP 工具并解锁
- 如果希望立即生效而不等待自动重建，可手动重启后端或调用 `get_agent().create()` 强制重建

### 4.4 Agent 滥用 tool_search

- `tool_search` 受 `wrap_with_budget()` 限制，计入 20 次工具调用上限
- 搜索返回已加载的工具时提示 "already_loaded"，引导 Agent 直接调用

### 4.5 兼容性

- `ToolDef` 新增 `meta` 字段有默认值（`ToolMeta()`），现有工具无需立即修改
- 逐步迁移：先给危险工具加 `always_on=False`，再扩展给其他工具
- 测试层面：`test_shell_tool.py` 等现有测试不受影响

---

## 5. 效果预期

### 5.1 Token 层面

| 场景 | 改造前（工具schema token） | 改造后（可见schema + deferred hint token） | 节省 |
|------|--------------------------|------------------------------------------|------|
| 基础对话（无MCP） | ~800（14个工具全量） | ~300（5个always_on）+ ~80（deferred目录） | ~60% |
| 接入 filesystem MCP | ~1500（25个工具全量） | ~350（5+3预加载）+ ~120（deferred目录，含MCP分组） | ~70% |
| 高频使用 web_search | ~800 | ~400（5+1预加载）+ ~80（deferred目录） | ~40% |

**说明**：deferred 目录 hint 的 token 开销已计入。20 个工具名 + MCP server 分组信息约 50–150 token，不影响"可见 schema 大幅减少」的核心结论。

### 5.2 用户体验层面

| 场景 | 改造前 | 改造后 |
|------|--------|--------|
| 闲聊 | Agent 可能误触 shell/file_write | Agent 看不到这些工具，更专注对话 |
| 查天气 | web_search 直接可用，可能误调用 | Agent 先 tool_search("web_search") 解锁，意图更明确 |
| 写代码 | shell/file_read/file_write 全量可见 | 首次需要搜索解锁，后续预加载直接可用 |
| 接入新 MCP | 工具列表暴涨，Agent 混乱 | 新 MCP 工具进 deferred 目录，按需解锁 |

### 5.3 安全层面

- `shell`、`file_write` 默认不可见 → 降低误操作概率
- 即使 Agent 搜索解锁了 shell，其 description 中的安全限制仍然生效
- `destructive` 风险等级未来可用于更细粒度的权限控制

---

## 6. 后续扩展方向

1. **用户级工具白名单**：在设置页面让用户关闭特定工具（如禁用 shell）
2. **自动预加载推断**：根据对话 topic 自动预加载相关工具（如提到"文件"时自动预加载 file_read）
3. **风险等级拦截**：frontend 在 Agent 调用 destructive 工具前弹出确认框
4. **工具使用统计**：记录每个工具的解锁频率，优化 always_on 策略
5. **向量搜索后端**：当工具数量 >100 时，将关键词搜索升级为语义搜索
6. **缓存持久化**：将 `ToolDiscoveryState` 的预加载缓存序列化到 `conversations.metadata_json`，重启后恢复

---

## 7. 决策记录（ADR）

**为什么不用 PydanticAI 原生的条件工具？**

PydanticAI 目前不支持运行时的工具动态增减（`toolsets` 在 `Agent` 创建时固定）。我们的方案是在 Agent 创建时传入过滤后的 schema，配合 system prompt 中的 deferred 目录提示 Agent 去调用 `tool_search`。

**为什么不把 tool_search 做成自动的（由系统推断而非 Agent 主动调用）？**

- 增加一层系统推断需要额外 LLM 调用，延迟 + 成本
- Agent 自己最清楚需要什么工具（它正在规划下一步）
- 元工具模式（tool_search）是 akashic-agent、OpenCode 等项目的验证过的成熟方案

**为什么 LRU 容量是 10？**

- always_on 约 5 个，preloaded 约 5-10 个，总计每轮 10-15 个工具
- 这个数量级下 function calling 的推理延迟和 token 消耗都可控
- 容量过小（如 3）会导致频繁搜索；过大（如 20）则失去动态加载的意义

---

## 8. 测试策略

实现完成后需验证以下场景：

### 8.1 单元测试（ToolRegistry）
- `search()` 匹配分数正确：name 匹配 > description 匹配 > search_hint 匹配 > tag 匹配
- `search()` 的 `excluded` 参数正确排除已可见工具
- `get_deferred_names()` 正确返回 builtin / mcp 分组，且不包含 always_on 和 visible
- `get_schemas(names)` 按传入名称过滤，不存在的名称静默跳过

### 8.2 单元测试（ToolDiscoveryState）
- 冷启动时 `get_visible()` 返回空列表
- `update()` 将工具按最近使用排序，去重，截断到 capacity
- `update()` 不缓存 always_on 工具
- 达到 `max_conversations` 时淘汰最久未更新的 conversation
- `clear()` 正确移除指定 conversation

### 8.3 单元测试（tool_search）
- `select:shell` 精确加载，写入 LRU
- 已加载的工具调用 `select:` 返回 `already_loaded`
- 关键词搜索返回匹配结果，并按风险等级过滤
- 解锁的工具在**同轮后续 tool loop**中不可见（验证 PydanticAI 限制）
- 解锁的工具在**下一轮 Agent run**中自动可见（验证 discovery 缓存生效）

### 8.4 集成测试（对话流程）
- 冷启动对话只有 always_on 工具（5 个），不含 deferred
- Agent 调用 `tool_search("web_search")` 后，下轮对话 `web_search` 直接可见
- 高频使用某工具 3 轮后，该工具仍在 preloaded 中
- 使用 11 个不同 deferred 工具后，最久未用的那个被淘汰，再次需要时需重新搜索
- system prompt 中 deferred 目录包含正确的工具名和 MCP server 分组

### 8.5 回归测试
- 现有 `test_shell_tool.py`、`test_memory_dedup.py` 等不失败
- `factory.assemble_tools()` 的接口兼容（或更新调用方）
- MCP 工具发现流程不受影响

---

*文档版本: v1.1*  
*作者: Kimi Code CLI*  
*日期: 2026-05-20*
