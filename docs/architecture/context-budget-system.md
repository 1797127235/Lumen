# Lumen Context Budget System — 架构设计 v2

> 基于 v1 review 修正。核心修正：完整结果在工具中间件层保留（而非工具层截断后再落盘），
> spill/reload 协议让模型可用，Layer 5 在正确的事务边界执行。

## 0. 三个基础决策

在分层设计之前，先收口三个 v1 中未澄清的前提：

### 决策 1：完整结果在哪一层保留？

**结论：工具中间件层（`_middleware.py`）是唯一能拿到完整返回值的拦截点。**

数据流：

```
Tool handler 返回 str（完整内容）
    ↓
wrap_with_* 中间件链（此时内容完整，可落盘）
    ↓
工具返回结果（已进入消息流）
    ↓
session.py _truncate_tool_returns_in_messages（此时只能看到 ToolReturnPart.content）
    ↓
持久化到 DB
```

工具 handler 的约束是 `-> str`（AGENTS.md line 31），不可能让 handler 直接返回结构化对象。
但中间件已经包裹了 `execute`，且是同步可改造的：
- `wrap_with_logging` 先拿到完整 result
- 它之后的 `wrap_with_budget` 拿到的也是完整 result
- 中间件可以在此处落盘完整内容，并替换为 preview

**工具自身不做截断**（L1 cap 降级为 fallback，只在无中间件时生效）。
中间件是落盘点，`_truncate_tool_returns_in_messages` 只做 cap 兜底。

### 决策 2：spill/reload 协议

**结论：注册一个 `result_read` 工具 + preview 中包含可读路径 + system prompt 约定。**

Agent 看到的 preview 格式：

```
<persisted-output tool="web_extract" result_id="call_abc123">
完整内容过大（31,247 字符），已保存。使用 result_read 工具可读取完整内容。
预览（前 800 字符）：
... 实际内容 ...
</persisted-output>
```

配套工具：

```python
# lib/tools/result_read.py
# 注册为 always_on 工具，Agent 始终可用
def handle_result_read(args, ctx):
    """读取之前因过大而落盘的工具返回完整内容"""
    result_id = args["result_id"]
    store = ToolResultStore(ctx.conversation_id)
    content = store.load(result_id)
    if not content:
        return tool_error("未找到该结果，可能已过期")
    # 加 offset/limit 支持分段读取
    offset = args.get("offset", 0)
    limit = args.get("limit", 8000)
    return tool_ok(content[offset:offset+limit])
```

system prompt 中追加约定（通过 `@agent.system_prompt` 装饰器，和现有 `_skills_prompt` 同级）：

```python
# core/agent.py create() 方法内，与 _skills_prompt 并列

@agent.system_prompt
async def _result_read_prompt(ctx: RunContext[LumenDeps]) -> str:
    return (
        "当你在上下文中看到 <persisted-output> 标签时，表示完整工具结果因过大已保存到磁盘。"
        "如果需要回顾完整内容，使用 result_read 工具（传入 result_id）读取。"
        "不需要主动读取，只在确实需要回顾细节时才调用。"
    )
```

这样 system prompt 的追加方式与现有 `_skills_prompt` 完全一致，不需要修改静态的 `build_system_prompt()`。

### 决策 3：Layer 5 的事务边界

**结论：压缩在 `save_pydantic_history` 内部执行，替代 `_safe_tail`，而不是在 `persist_turn` 中。**

当前 `persist_turn` 的流程：

```python
# persistence.py line 64
save_pydantic_history(conv, state.new_msgs)  # ← 这里写 conv.pydantic_messages
await db.commit()                             # ← 这里提交
```

`save_pydantic_history` 内部：

```python
# session.py line 465-497
existing = load_pydantic_history(conv)
updated = existing + truncated_new_msgs
updated = sanitize_history(updated)
if len(updated) <= _MAX_HISTORY_MESSAGES:
    conv.pydantic_messages = to_json(updated).decode()
    return
# 超限时：
tail = _safe_tail(updated, _MAX_HISTORY_MESSAGES)  # ← 当前直接丢弃旧 turn
conv.pydantic_messages = to_json(tail).decode()
```

**正确的 Layer 5 接入点**：替换 `_safe_tail` 为压缩逻辑：

```python
if len(updated) <= _MAX_HISTORY_MESSAGES:
    conv.pydantic_messages = to_json(updated).decode()
    return

# 超限时不再直接丢弃，而是压缩
compressed = await compress_history(updated)  # 替代 _safe_tail
conv.pydantic_messages = to_json(compressed).decode()
```

`save_pydantic_history` 当前是同步函数，压缩需要调 LLM（async）。
**解决方案**：通过 `persist_turn`（已经是 async）在 `save_pydantic_history` 之前触发压缩，把压缩结果传入 `save_pydantic_history`。

```python
# persistence.py — persist_turn（已经是 async）

async def persist_turn(db, conv, state, user_id, user_input, agent_generation, deps):
    # ... 现有 token usage 记录、Message 写入 ...

    # 压缩检查（在 save_pydantic_history 之前）
    existing = load_pydantic_history(conv)
    if len(existing) + len(state.new_msgs) > _MAX_HISTORY_MESSAGES:
        compressor = get_context_compressor()
        if compressor.should_compress(existing + state.new_msgs):
            existing = await compressor.compress(existing)
            # 压缩后的 existing 直接替换 conv 中的历史
            # save_pydantic_history 会基于此追加 new_msgs

    save_pydantic_history(conv, state.new_msgs, base_history=existing)
    # save_pydantic_history 仍然是同步的，只是接收预压缩的历史

# session.py — save_pydantic_history 签名变更
def save_pydantic_history(conv, new_msgs, *, base_history=None):
    """持久化消息历史。base_history 为外部预压缩的历史（可选）。"""
    truncated_new_msgs = _truncate_tool_returns_in_messages(new_msgs)

    if base_history is not None:
        updated = base_history + truncated_new_msgs
    else:
        existing = load_pydantic_history(conv)
        updated = existing + truncated_new_msgs

    updated = sanitize_history(updated)

    if len(updated) <= _MAX_HISTORY_MESSAGES:
        conv.pydantic_messages = to_json(updated).decode()
        return

    # 超限：截断兜底（压缩已在调用方完成，这里处理压缩后仍超限的边界情况）
    tail = _safe_tail(updated, _MAX_HISTORY_MESSAGES)
    conv.pydantic_messages = to_json(tail).decode()
```

这样：
- 压缩在 async 上下文（`persist_turn`）中执行
- `save_pydantic_history` 保持同步，通过参数接收预压缩历史
- 不会出现压缩结果被后续保存冲掉的问题（压缩在 save 之前完成）

---

## 1. 分层架构（修正后）

```
数据流（自上而下，每一层只能看到上一层处理后的结果）：

Tool handler 返回 str
    ↓
┌─────────────────────────────────────────────────────────────────┐
│  Layer 0: 工具中间件 — 结果落盘（唯一能拿到完整内容的地方）     │
│  wrap_with_result_budget → 完整内容写文件 → 替换为 preview      │
│  位置: _middleware.py (factory.py 中间件链)                      │
│  时机: handler 返回后，消息流包装前                         │
├─────────────────────────────────────────────────────────────────┤
│  Layer 1: 单轮总量预算                                          │
│  event_handlers.py agent_run_result → 检查本轮总量 → 额外落盘   │
│  时机: Agent Run 结束后，persist_turn 前                        │
├─────────────────────────────────────────────────────────────────┤
│  Layer 2: 持久化时 cap 兜底                                     │
│  session.py _truncate_tool_returns → 对未落盘的小结果做 cap     │
│  时机: save_pydantic_history                                    │
├─────────────────────────────────────────────────────────────────┤
│  Layer 3: Pre-Request 剪枝（幂等）                              │
│  core/agent.py ProcessHistory → 旧工具返回替换为摘要            │
│  时机: 每次 API 调用前                                          │
├─────────────────────────────────────────────────────────────────┤
│  Layer 4: LLM 压缩（替代 _safe_tail）                          │
│  session.py save_pydantic_history → 异步压缩 → 下次加载生效     │
│  时机: 历史超 40 条时                                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 各层详细设计

### Layer 0: 工具中间件 — 结果落盘

**关键约束**：这是唯一拿到完整结果的拦截点。

**tool_call_id 问题**：中间件 execute 函数接收 `(args, deps)`，其中 `deps` 是 `LumenDeps`，
不是 `RunContext`。`tool_call_id` 只存在于 `RunContext.tool_call_id`，中间件拿不到。

解决方案：修改 `_to_pydantic_tool`（factory.py:160-178），将 `RunContext` 而非 `ctx.deps`
传给 execute。这样 execute 的第二个参数就是完整的 `RunContext[LumenDeps]`，
可以访问 `ctx.tool_call_id`、`ctx.deps.conversation_id` 等。

```python
# factory.py — _to_pydantic_tool 修改

def _to_pydantic_tool(t: ToolDef):
    from pydantic_ai import RunContext
    from pydantic_ai.tools import Tool
    from core.agent import LumenDeps

    async def handler(ctx: RunContext[LumenDeps], **kwargs):
        return await t.execute(kwargs, ctx)  # ← 传 ctx 而非 ctx.deps

    handler.__name__ = t.name
    handler.__doc__ = t.description

    return Tool.from_schema(
        function=handler,
        name=t.name,
        description=t.description,
        json_schema=t.input_schema,
        takes_ctx=True,
        sequential=not t.read_only,
    )
```

**签名变更影响**：所有现有中间件的第二个参数从 `LumenDeps` 变为 `RunContext[LumenDeps]`。
需要同步修改现有中间件的属性访问（`deps.conversation_id` → `deps.deps.conversation_id`），
或在中间件内部解构：

```python
# 现有中间件适配模式（以 wrap_with_budget 为例）：

async def budgeted(args: dict[str, Any], ctx, _orig=orig, _name=t.name):
    # ctx 现在是 RunContext[LumenDeps]
    deps = ctx.deps if hasattr(ctx, 'deps') else ctx  # 兼容两种调用方式
    used = deps.usage_budget.get("calls", 0)
    ...
```

但更干净的做法是：让中间件函数内部统一 `deps = ctx.deps`，一次性改完。
所有中间件（logging / budget / failure_degradation / result_budget）都走这个模式。

**注意**：工具返回值经过处理后进入消息流：
- handler 通过 `tool_ok()` / `tool_error()` 返回结果
- 返回值被包装为消息流中的 content 进入消息流
- 中间件看到的是 handler 的原始返回值（`str`）

```python
# lib/tools/_middleware.py — 新增

_RESULT_PERSIST_THRESHOLD = 6_000  # 字符
_PREVIEW_SIZE = 800

def wrap_with_result_budget(tools: list[ToolDef]) -> list[ToolDef]:
    """结果预算中间件：大结果落盘，返回 preview。

    在中间件链中的位置（factory.py）：
    wrap_with_failure_degradation → wrap_with_logging → wrap_with_result_budget → wrap_with_budget
    保证 logging 能记录完整结果大小，result_budget 能拿到未截断的原始内容。

    注意：ctx 参数是 RunContext[LumenDeps]（由 _to_pydantic_tool 传入），
    通过 ctx.deps 访问 LumenDeps，ctx.tool_call_id 获取调用 ID。
    """

    def _extract_text(result) -> str:
        """从 handler 返回值中提取文本（兼容 str 和 ToolReturn）"""
        if hasattr(result, "return_value"):
            return str(result.return_value)
        return str(result)

    def _rebuild_result(result, new_text):
        """用新文本重建返回值，保留原有 ToolReturn 的 metadata"""
        from pydantic_ai import ToolReturn
        if isinstance(result, ToolReturn):
            return ToolReturn(
                return_value=new_text,
                metadata=result.metadata,
            )
        return new_text

    def wrap(t: ToolDef) -> ToolDef:
        orig = t.execute

        async def budgeted(args: dict[str, Any], ctx, _orig=orig, _name=t.name):
            result = await _orig(args, ctx)
            text = _extract_text(result)

            # 不处理错误返回
            if text.startswith("❌"):
                return result

            # 小结果直接返回
            if len(text) <= _RESULT_PERSIST_THRESHOLD:
                return result

            # 从 RunContext 提取关键信息
            deps = ctx.deps if hasattr(ctx, "deps") else ctx
            conv_id = getattr(deps, "conversation_id", None)
            call_id = getattr(ctx, "tool_call_id", None) or _generate_call_id(_name, args)

            # 大结果：落盘完整内容，替换为 preview
            if conv_id:
                store = ToolResultStore(conv_id)
                store.save(_name, call_id, text)
                preview = _generate_preview(text, _PREVIEW_SIZE)
                replacement = (
                    f"<persisted-output tool=\"{_name}\" result_id=\"{call_id}\">\n"
                    f"完整内容过大（{len(text):,} 字符），已保存。"
                    f"使用 result_read 工具读取完整内容。\n"
                    f"预览：\n{preview}\n"
                    f"</persisted-output>"
                )
                return _rebuild_result(result, replacement)

            # 无 conv_id 时 fallback 截断
            cap = get_budget_config().tool_output_caps.get(_name, 4000)
            if len(text) > cap:
                truncated = text[:cap] + f"\n...({len(text) - cap} chars truncated)..."
                return _rebuild_result(result, truncated)

            return result

        return dataclasses.replace(t, execute=budgeted)

    return [wrap(t) for t in tools]

def _generate_call_id(tool_name: str, args: dict) -> str:
    """当 RunContext.tool_call_id 不可用时，生成稳定的 fallback ID"""
    import hashlib
    raw = f"{tool_name}:{json.dumps(args, sort_keys=True, default=str)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

**中间件注册顺序**（factory.py）：

```python
# factory.py register_all_tools()
# 顺序：最内层最先执行，最外层最后执行
all_tools = wrap_with_failure_degradation(all_tools)  # 最内：检测连续失败
all_tools = wrap_with_logging(all_tools)               # 记录完整结果日志
all_tools = wrap_with_result_budget(all_tools)          # ← 新增：落盘
all_tools = wrap_with_budget(all_tools, limit=20)       # 最外：调用次数限制
```

### ToolResultStore（同步，纯文件 IO）

```python
# lib/chat/context_budget.py

class ToolResultStore:
    """管理工具返回的溢出落盘。纯同步文件 IO，不涉及 async。"""

    def __init__(self, conv_id: str):
        self._dir = Path.home() / ".lumen" / "tool_results" / conv_id

    def save(self, tool_name: str, call_id: str, content: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{call_id}.json"
        path.write_text(json.dumps({
            "tool_name": tool_name,
            "content": content,
            "char_count": len(content),
            "saved_at": datetime.now(UTC).isoformat(),
        }, ensure_ascii=False), encoding="utf-8")

    def load(self, call_id: str) -> str | None:
        path = self._dir / f"{call_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["content"]

    def cleanup(self, max_age_days: int = 7) -> int:
        """清理该会话下的过期落盘文件"""
        if not self._dir.exists():
            return 0
        cutoff = datetime.now(UTC).timestamp() - max_age_days * 86400
        removed = 0
        for f in self._dir.glob("*.json"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        return removed

    @classmethod
    def cleanup_all(cls, max_age_days: int = 7) -> int:
        """清理所有会话的过期落盘文件"""
        base = Path.home() / ".lumen" / "tool_results"
        if not base.exists():
            return 0
        total = 0
        for conv_dir in base.iterdir():
            if conv_dir.is_dir():
                store = cls(conv_dir.name)
                total += store.cleanup(max_age_days)
            # 清理空目录
            try:
                conv_dir.rmdir()
            except OSError:
                pass
        return total

    @classmethod
    def remove_conv_dir(cls, conv_id: str) -> None:
        """删除整个会话的落盘目录（会话删除时调用）"""
        conv_dir = Path.home() / ".lumen" / "tool_results" / conv_id
        if conv_dir.exists():
            import shutil
            shutil.rmtree(conv_dir, ignore_errors=True)
```

**cleanup 调用时机**（两个入口）：

1. **会话删除时**（server/routes/chat.py `delete_conversation`，line 158-177）：

```python
# chat.py delete_conversation — 在现有 cleanup_session_files 之后追加：

from lib.chat.session_files import cleanup_session_files
await cleanup_session_files(conversation_id)

# ← 新增：清理该会话的落盘工具结果
from lib.chat.context_budget import ToolResultStore
ToolResultStore.remove_conv_dir(conversation_id)
```

2. **启动时扫描清理**（core/startup.py `lifespan`，line 70-201）：

```python
# startup.py lifespan — 在 migrate_md_files 之后追加：

from core.migrations import migrate_md_files
await migrate_md_files()

# ← 新增：清理过期的落盘工具结果（7 天 TTL）
from lib.chat.context_budget import ToolResultStore
cleaned = ToolResultStore.cleanup_all(max_age_days=7)
if cleaned:
    logger.info("tool_results_cleanup", removed=cleaned)
```
```

### result_read 工具（配套 reload 协议）

```python
# lib/tools/result_read.py

def create_result_read_tool() -> ToolDef:
    return ToolDef(
        name="result_read",
        description=(
            "读取之前因过大而落盘的工具返回完整内容。"
            "当你在上下文中看到 <persisted-output> 标签时，"
            "可使用此工具读取完整结果。支持 offset/limit 分段读取。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "result_id": {"type": "string", "description": "persisted-output 标签中的 result_id"},
                "offset": {"type": "integer", "description": "起始字符位置（默认 0）", "default": 0},
                "limit": {"type": "integer", "description": "读取字符数（默认 8000，最大 20000）", "default": 8000},
            },
            "required": ["result_id"],
        },
        execute=_handle_result_read,
        meta=ToolMeta(risk="read-only", always_on=True),
    )

async def _handle_result_read(args: dict, ctx) -> str:
    from lib.chat.context_budget import ToolResultStore

    conv_id = getattr(ctx, "conversation_id", None)  # 从 deps 获取
    if not conv_id:
        return "❌ 无法确定会话 ID"

    store = ToolResultStore(conv_id)
    result_id = args["result_id"]
    content = store.load(result_id)
    if not content:
        return f"❌ 未找到结果 {result_id}，可能已过期"

    offset = args.get("offset", 0)
    limit = min(args.get("limit", 8000), 20_000)
    chunk = content[offset:offset + limit]

    if offset + limit < len(content):
        chunk += f"\n\n... (还有 {len(content) - offset - limit:,} 字符未读取，调整 offset 继续读取)"
    return chunk
```

**注册**：在 `factory.py` 的 `register_all_tools()` 中加入 `all_tools` 列表，
因为 `always_on=True`，Agent 始终可见，不需要 `tool_search` 解锁。

### Layer 1: 单轮总量预算

**位置**: `event_handlers.py` 的 `agent_run_result` handler
**时机**: Agent Run 结束后，`state.new_msgs` 已收集完毕，`persist_turn` 调用前

```python
# lib/chat/context_budget.py

def enforce_turn_budget(
    messages: list[ModelMessage],
    conv_id: str,
    budget: int = 30_000,
) -> list[ModelMessage]:
    """同步函数。检查单轮工具返回总量，超预算时最大的结果额外落盘。

    注意：Layer 0 已经处理了单条大结果。
    这一层处理的是「多条中等结果合计超预算」的情况。
    """
    from dataclasses import replace
    from pydantic_ai.messages import RetryPromptPart, ToolReturnPart

    # 收集所有非 persisted-output 的工具返回及其大小
    tool_returns: list[tuple[int, int, int]] = []  # (msg_idx, part_idx, char_count)
    for i, msg in enumerate(messages):
        if not isinstance(msg, ModelRequest):
            continue
        for j, p in enumerate(msg.parts):
            if not isinstance(p, ToolReturnPart | RetryPromptPart):
                continue
            content = str(getattr(p, "content", ""))
            # 已落盘的跳过（检查 <persisted-output> 标记）
            if "<persisted-output" in content:
                continue
            tool_returns.append((i, j, len(content)))

    total = sum(sz for _, _, sz in tool_returns)
    if total <= budget:
        return messages

    # 按大小降序，优先落盘最大的
    tool_returns.sort(key=lambda x: x[2], reverse=True)
    store = ToolResultStore(conv_id)
    messages = list(messages)  # 浅拷贝，不修改原列表

    for i, j, sz in tool_returns:
        if total <= budget:
            break
        msg = messages[i]
        p = msg.parts[j]
        content = str(getattr(p, "content", ""))
        call_id = getattr(p, "tool_call_id", f"spill_{i}_{j}")

        store.save("turn_budget_spill", call_id, content)
        preview = _generate_preview(content, _PREVIEW_SIZE)
        replacement = (
            f"<persisted-output tool=\"turn_budget_spill\" result_id=\"{call_id}\">\n"
            f"单轮总量超预算，完整内容已保存。使用 result_read 工具读取。\n"
            f"预览：\n{preview}\n"
            f"</persisted-output>"
        )

        new_parts = list(msg.parts)
        new_parts[j] = replace(p, content=replacement)
        messages[i] = replace(msg, parts=new_parts)
        total -= sz - len(replacement)

    logger.info("turn_budget_enforced", spilled=len(tool_returns), total_chars=total)
    return messages
```

**调用点**（event_handlers.py `agent_run_result` handler，line 84-93）：

```python
# event_handlers.py — EventHandlers.agent_run_result

@staticmethod
def agent_run_result(event, state, deps: dict[str, Any]) -> list[dict]:
    state.new_msgs = event.result.new_messages()  # ← new_msgs 在此收集完毕

    # ← Layer 1 插入点：在 new_msgs 收集后、返回前
    from lib.chat.context_budget import enforce_turn_budget
    conv_id = deps.get("conversation_id")
    if conv_id:
        state.new_msgs = enforce_turn_budget(state.new_msgs, conv_id)

    if not state.cancelled:
        try:
            from shared.llm_usage import extract_usage
            state.usage_data = extract_usage(event.result.usage)
        except Exception:
            pass
    return []
```

`conv_id` 从 `deps["conversation_id"]` 取（agent_runner.py line 215 传入，格式见 EVENT_HANDLERS dict）。
`state.new_msgs` 在 `event.result.new_messages()` 后已包含本轮所有消息（含 ToolCall/ToolReturn）。
`enforce_turn_budget` 是同步函数，不阻塞事件循环。

### Layer 2: 持久化时 cap 兜底

**保持现有的 `_truncate_tool_returns_in_messages`**，但简化为只处理未被 Layer 0 落盘的小结果：

```python
# session.py — 现有函数，逻辑不变
# 对未被 <persisted-output> 替换的 ToolReturnPart 做 cap 截断
# 已经是 persisted-output 的不会被二次处理（因为 <persisted-output> 本身就 < cap）
```

不需要改动。现有的 2000 字符 cap 对已被 Layer 0 落盘的结果无效（preview 本身约 800 字符），
对未被落盘的小结果（< 6000 字符）按 2000 字符截断作为兜底。

### Layer 3: Pre-Request 剪枝（幂等）

**关键修正**：用哨兵标记已摘要的结果，避免重复摘要。

```python
# lib/chat/context_budget.py

_SUMMARY_PREFIX = "[PRUNED]"  # 哨兵：已剪枝的工具返回以此开头

def prune_old_tool_results(
    messages: list[ModelMessage],
    protect_tail_turns: int = 3,
) -> list[ModelMessage]:
    """幂等的旧工具结果剪枝。

    幂等性保证：
    - 已以 [PRUNED] 开头的结果不会被再次处理
    - 已是 <persisted-output> 的结果不会被处理
    """
    from dataclasses import replace
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart, RetryPromptPart

    turns = _find_turn_boundaries(messages)
    if len(turns) <= protect_tail_turns:
        return messages

    protected_start = turns[-protect_tail_turns][0]
    modified = False
    result = list(messages)

    for i in range(protected_start):
        msg = result[i]
        if not isinstance(msg, ModelRequest):
            continue

        has_tool = any(isinstance(p, ToolReturnPart | RetryPromptPart) for p in msg.parts)
        if not has_tool:
            continue

        new_parts = []
        for p in msg.parts:
            if isinstance(p, ToolReturnPart | RetryPromptPart):
                content = str(getattr(p, "content", ""))

                # 幂等检查：已剪枝或已落盘的不动
                if content.startswith(_SUMMARY_PREFIX) or "<persisted-output" in content:
                    new_parts.append(p)
                    continue

                tool_name = _find_tool_name_for_call_id(result, p.tool_call_id, i)
                summary = f"{_SUMMARY_PREFIX} [{tool_name}] {content[:150].replace(chr(10), ' ').strip()}... ({len(content):,} chars)"
                new_parts.append(replace(p, content=summary))
                modified = True
            else:
                new_parts.append(p)

        result[i] = replace(msg, parts=new_parts)

    return result if modified else messages
```

**集成到 ProcessHistory**（core/agent.py）：

`ProcessHistory` 通过 `_run_history_processor` 分发，**同时支持同步和异步函数**
（用 `is_async_callable` 检测，同步函数会通过 `run_in_executor` 执行）。
`prune_old_tool_results` 是纯同步函数（无 LLM 调用），直接传同步函数即可：

```python
# core/agent.py — 保持同步（和现有 _clean_orphaned_tool_parts 一致）

def _lumen_process_history(messages):
    messages = _clean_orphaned_tool_parts(messages)
    messages = prune_old_tool_results(messages, protect_tail_turns=3)
    return messages

# agent.py 中（不变）：
capabilities=[
    ReinjectSystemPrompt(),
    ProcessHistory(_lumen_process_history),
]
```

### Layer 4: LLM 压缩（替代 _safe_tail）

见决策 3 中的设计。核心改动：

1. `save_pydantic_history` 超限时先截断（同步），同时创建异步压缩任务
2. 压缩任务在后台执行，写回 DB，下次加载时生效
3. 压缩逻辑调用 `on_pre_compress` 钩子（现有但未使用）

### Turn 边界修正

现有的 `_find_turn_boundaries` 把任意 `ModelRequest` 都当 turn 起点（session.py:136）。
Layer 3/4 需要按"用户轮次"工作（包含 UserPromptPart 的 ModelRequest 才是新 turn）。

```python
# lib/chat/context_budget.py

def _find_user_turn_boundaries(messages: list[ModelMessage]) -> list[tuple[int, int]]:
    """识别用户轮次边界。

    用户轮次 = 包含 UserPromptPart 的 ModelRequest 开始，
    到下一个包含 UserPromptPart 的 ModelRequest 之前。
    """
    from pydantic_ai.messages import UserPromptPart

    if not messages:
        return []

    # 找到所有包含 UserPromptPart 的 ModelRequest 的索引
    user_msg_indices = []
    for i, msg in enumerate(messages):
        if isinstance(msg, ModelRequest):
            has_user = any(isinstance(p, UserPromptPart) for p in msg.parts)
            if has_user:
                user_msg_indices.append(i)

    if not user_msg_indices:
        return [(0, len(messages) - 1)]

    turns = []
    for idx, start in enumerate(user_msg_indices):
        end = user_msg_indices[idx + 1] - 1 if idx + 1 < len(user_msg_indices) else len(messages) - 1
        turns.append((start, end))

    return turns
```

Layer 3/4 使用 `_find_user_turn_boundaries`，现有 `_find_turn_boundaries` 不动。

---

## 3. BudgetConfig

```python
# lib/chat/context_budget.py

@dataclass(frozen=True)
class BudgetConfig:
    # Layer 0: 单条落盘阈值
    result_persist_threshold: int = 6_000
    preview_size: int = 800

    # Layer 0 fallback: 工具输出 cap（无 conv_id 时）
    tool_output_caps: dict[str, int] = field(default_factory=lambda: {
        "web_extract": 8_000,
        "shell":      10_000,
        "file_read":  10_000,
        "default":    4_000,
    })

    # Layer 1: 单轮总量
    turn_budget_chars: int = 30_000

    # Layer 3: 保护最近 N 个用户轮次
    protect_tail_turns: int = 3

    # Layer 4: 压缩触发阈值（历史消息条数）
    compress_trigger_messages: int = 35  # 接近 40 上限时触发

    # 落盘文件过期天数
    result_max_age_days: int = 7
```

可通过 `~/.lumen/config.json` 的 `context_budget` 字段覆盖。

---

## 4. 文件清单与改动范围

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `lib/chat/context_budget.py` | **新增** | BudgetConfig, ToolResultStore, enforce_turn_budget, prune_old_tool_results, _find_user_turn_boundaries |
| `lib/tools/result_read.py` | **新增** | result_read 工具（reload 协议） |
| `lib/tools/_middleware.py` | 修改 | 新增 `wrap_with_result_budget`；**现有三个中间件适配 `ctx` 为 RunContext** |
| `lib/tools/factory.py` | 修改 | **`_to_pydantic_tool` 传 `ctx` 而非 `ctx.deps`**；中间件链加入 `wrap_with_result_budget`；all_tools 加入 `result_read` |
| `core/agent.py` | 修改 | ProcessHistory 集成 Layer 3 剪枝；`@agent.system_prompt` 追加 result_read 约定 |
| `lib/chat/session.py` | 修改 | `save_pydantic_history` 接收 `base_history` 参数；超限时走压缩路径 |
| `lib/chat/persistence.py` | 修改 | `persist_turn` 中执行压缩，传入 `base_history` |
| `lib/chat/event_handlers.py` | 修改 | agent_run_result handler 中调用 `enforce_turn_budget` |
| `server/routes/chat.py` | 修改 | delete_conversation 中清理落盘文件 |
| `core/startup.py` | 修改 | lifespan 中启动时清理过期落盘文件 |

### 前置改动：`_to_pydantic_tool` 签名变更

这是 Phase 1 的第一步，必须在所有中间件工作之前完成：

```python
# factory.py _to_pydantic_tool — 改动点仅一行

async def handler(ctx: RunContext[LumenDeps], **kwargs):
    return await t.execute(kwargs, ctx)  # ctx 而非 ctx.deps
```

**现有中间件适配**（wrap_with_logging / wrap_with_budget / wrap_with_failure_degradation）：

三个中间件的第二个参数从 `LumenDeps` 变为 `RunContext[LumenDeps]`。
统一用 `deps = ctx.deps if hasattr(ctx, 'deps') else ctx` 获取 LumenDeps，
保证向后兼容（测试中可能直接传 LumenDeps）。

以 `wrap_with_budget` 为例：

```python
async def budgeted(args: dict[str, Any], ctx, _orig=orig, _name=t.name):
    deps = ctx.deps if hasattr(ctx, 'deps') else ctx
    used = deps.usage_budget.get("calls", 0)
    if used >= limit:
        return tool_error(f"工具调用次数已达上限 ({used}/{limit})，请直接回答", "BUDGET")
    result = await _orig(args, ctx)  # ← 向下传 ctx（不是 deps）
    deps.usage_budget["calls"] = used + 1
    return result
```

同理 `wrap_with_logging` 和 `wrap_with_failure_degradation` 各加一行 `deps = ctx.deps if hasattr(...)`。

---

## 5. 实施路径

### Phase 0: 前置改动（`_to_pydantic_tool` + 中间件适配）

1. 修改 `factory.py` `_to_pydantic_tool`：`ctx.deps` → `ctx`
2. 修改 `_middleware.py` 三个现有中间件：加 `deps = ctx.deps if hasattr(ctx, 'deps') else ctx`
3. 跑 `pytest` 确认无回归

### Phase 1: Layer 0 + result_read（解决 77K 问题）

4. 创建 `lib/chat/context_budget.py`（BudgetConfig + ToolResultStore + _generate_preview）
5. 创建 `lib/tools/result_read.py`
6. 在 `_middleware.py` 新增 `wrap_with_result_budget`
7. 在 `factory.py` 注册中间件 + result_read 工具
8. `core/agent.py` 追加 `@agent.system_prompt` result_read 约定

**预期效果**: web_extract 31K 内容 → 落盘 + 800 字符 preview，消除 input 爆炸

### Phase 2: Layer 1 + Layer 3

9. 实现 `enforce_turn_budget`，在 event_handlers.py 中调用
10. 实现 `prune_old_tool_results`（幂等），集成到 ProcessHistory
11. 实现 `_find_user_turn_boundaries`

**预期效果**: 密集工具调用总量受控，旧工具返回自动摘要

### Phase 3: Layer 4

12. 实现 `compress_history`
13. 修改 `save_pydantic_history` 接收 `base_history`
14. 修改 `persist_turn` 执行压缩
15. 激活 `on_pre_compress` 钩子

**预期效果**: 长对话自动压缩，不再丢弃旧 turn

---

## 6. 监控指标

在 `_log_history_stats` 和中间件日志中新增：

| 指标 | 来源 | 说明 |
|------|------|------|
| `result_persisted` | Layer 0 中间件 | 本轮落盘的工具返回数 + 总字符数 |
| `turn_budget_spilled` | Layer 1 | 单轮预算触发时的额外落盘数 |
| `pruned_old_results` | Layer 3 | 被摘要替换的旧工具返回数 |
| `compression_triggered` | Layer 4 | 是否触发了 LLM 压缩 |
| `compression_ratio` | Layer 4 | 压缩前后消息条数比 |
| `result_read_called` | result_read 工具 | Agent 回读落盘内容的次数 |
