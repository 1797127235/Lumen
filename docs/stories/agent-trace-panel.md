# Story: Agent Trace 面板

## 背景

Lumen 后端使用 PydanticAI Agent，每次回复都会调用 `memory_search`、`memory_save` 等工具，但用户完全看不到这个过程。

目标：在 AssistantBubble 上方显示工具调用过程（调用了什么工具、参数、结果），流式进行时展开，完成后折叠。效果类似 DeepTutor 的"思考 · 用时 6s / 观察 · 用时 11s"面板。

实现路径：将 `stream_chat_ws()` 内部的 `run_stream()` + `stream_text()` 替换为 `run_stream_events()`，后者在流式过程中逐个 yield 结构化事件，包含工具调用和结果。

---

## 可用的 PydanticAI 事件（已在 pydantic-ai 1.89.1 验证）

```python
from pydantic_ai import AgentRunResultEvent
from pydantic_ai.messages import (
    FunctionToolCallEvent,   # event_kind = 'function_tool_call'
    FunctionToolResultEvent, # event_kind = 'function_tool_result'
    PartDeltaEvent,          # event_kind = 'part_delta'
    TextPartDelta,           # delta.part_delta_kind = 'text'
)
```

关键字段：
- `FunctionToolCallEvent.part.tool_name` → 工具名
- `FunctionToolCallEvent.part.args` → 参数（`str | dict | None`）
- `FunctionToolResultEvent.content` → 工具返回内容（`str | None`）
- `PartDeltaEvent.delta`（如为 `TextPartDelta`）→ `.content_delta` 文本增量
- `AgentRunResultEvent.event_kind == 'agent_run_result'` → 流结束
- `AgentRunResultEvent.result.usage()` → 返回 Usage 对象，有 `.request_tokens`、`.response_tokens`

---

## 变更范围（4 个文件）

---

### 1. `app/backend/services/chat_service.py`

**改动：** 仅修改 `stream_chat_ws()` 函数内的 Agent 调用段（`stream_chat()` 不动）。将 `async with agent.run_stream(...)` + `response.stream_text()` 替换为 `async for event in agent.run_stream_events(...)`。无需新增辅助函数，直接 yield dict。

#### 替换 Agent 调用段

**删除（`stream_chat_ws()` 内部的 try 块中）：**
```python
        try:
            async with agent.run_stream(
                user_input,
                deps=deps,
                model_settings=ModelSettings(max_tokens=4096),
            ) as response:
                async for text in response.stream_text(delta=True):
                    if cancel_event.is_set():
                        cancelled = True
                        break
                    full_content += text
                    yield {"type": "token", "content": text, "conversation_id": conv.conversation_id}

                if not cancelled:
                    try:
                        u = response.usage()
                        usage_data = {
                            "input": u.request_tokens or 0,
                            "output": u.response_tokens or 0,
                        }
                    except Exception:
                        pass

        finally:
```

**替换为：**
```python
        try:
            from pydantic_ai import AgentRunResultEvent
            from pydantic_ai.messages import (
                FunctionToolCallEvent,
                FunctionToolResultEvent,
                PartDeltaEvent,
                TextPartDelta,
            )

            async for event in agent.run_stream_events(
                user_input,
                deps=deps,
                model_settings=ModelSettings(max_tokens=4096),
            ):
                if cancel_event.is_set():
                    cancelled = True
                    break

                ek = event.event_kind

                if ek == 'function_tool_call':
                    args = event.part.args
                    args_str = (
                        json.dumps(args, ensure_ascii=False)
                        if isinstance(args, dict)
                        else (args or "")
                    )
                    yield {
                        "type": "trace",
                        "kind": "call",
                        "tool": event.part.tool_name,
                        "content": args_str[:300],
                    }

                elif ek == 'function_tool_result':
                    content = event.content or ""
                    if isinstance(content, str):
                        yield {
                            "type": "trace",
                            "kind": "result",
                            "tool": "",
                            "content": content[:500],
                        }

                elif ek == 'part_delta':
                    if isinstance(event.delta, TextPartDelta):
                        text = event.delta.content_delta
                        full_content += text
                        yield {"type": "token", "content": text, "conversation_id": conv.conversation_id}

                elif ek == 'agent_run_result':
                    if not cancelled:
                        try:
                            u = event.result.usage()
                            usage_data = {
                                "input": u.request_tokens or 0,
                                "output": u.response_tokens or 0,
                            }
                        except Exception:
                            pass

        finally:
```

`finally` 块（保存 DB、触发投影、后台记忆审查）**完全不变**，原样保留。

**注意：**
- `run_stream_events()` 是普通 `async for` 循环，不是 `async with` 上下文管理器
- `cancelled`、`usage_data`、`full_content` 变量在替换段之前已声明（保持不变）
- `finally` 里的 `if cancelled: return` 以及 `yield {"type": "cancelled", ...}` 保持不变

---

### 2. `app/frontend/src/lib/wsClient.ts`

**改动①：** `WSHandlers` 类型新增 `onTrace` 回调：

```ts
export type WSHandlers = {
  onToken: (delta: string, conversationId: string) => void
  onDone: (conversationId: string, usage?: { input: number; output: number }) => void
  onCancelled: () => void
  onError: (message: string) => void
  onTrace: (kind: 'call' | 'result', tool: string, content: string) => void
}
```

**改动②：** `handleMessage` 的 switch 新增 `'trace'` case：

```ts
case 'trace':
  this.handlers.onTrace(
    String(msg.kind ?? 'call') as 'call' | 'result',
    String(msg.tool ?? ''),
    String(msg.content ?? ''),
  )
  break
```

加在 `'pong'` case 之前即可。

---

### 3. `app/frontend/src/lib/chatSession.tsx`

#### 改动①：`TraceEntry` 类型 + `ChatMessage` 新增 `traces`

在 `ChatMessage` 类型定义之前加：

```ts
export type TraceEntry = {
  tool: string
  args: string       // call 事件的 content
  result: string     // result 事件的 content（初始为空）
  done: boolean      // result 到达时置 true
}
```

`ChatMessage` 新增 `traces` 字段：

```ts
export type ChatMessage = {
  role: 'user' | 'assistant'
  content: string
  usage?: { input: number; output: number }
  traces?: TraceEntry[]
}
```

#### 改动②：`ChatWS` 构造函数中加 `onTrace` handler

在 `useEffect` 内 `new ChatWS({...})` 的 handlers 对象里加：

```ts
onTrace: (kind, tool, content) => {
  if (completedRef.current) return
  setMessages((prev) => {
    const next = prev.slice()
    const last = next[next.length - 1]
    if (!last || last.role !== 'assistant') return prev

    const traces = last.traces ? [...last.traces] : []

    if (kind === 'call') {
      traces.push({ tool, args: content, result: '', done: false })
    } else {
      const idx = traces.map((t) => t.done).lastIndexOf(false)
      if (idx >= 0) {
        traces[idx] = { ...traces[idx], result: content, done: true }
      }
    }

    next[next.length - 1] = { ...last, traces }
    return next
  })
},
```

加在 `onError` handler 之后即可。

**不改动**：`loadConversation`、`startNew`、`sendMessage`、`cancelStreaming`、`onDone`、`onCancelled`、`onToken`。

---

### 4. `app/frontend/src/pages/Chat.tsx`

#### 改动①：新增 `TracePanel` 组件

放在 `ThinkingCard` 之后、`InputBox` 之前：

```tsx
const TOOL_LABELS: Record<string, string> = {
  memory_search: '搜索记忆',
  memory_save: '保存记忆',
  update_profile: '更新画像',
  get_profile: '读取画像',
}

function TracePanel({ traces, streaming }: {
  traces: import('../lib/chatSession').TraceEntry[]
  streaming: boolean
}) {
  if (!traces.length) return null

  return (
    <div className="mb-sm flex flex-col gap-2xs">
      {traces.map((trace, i) => (
        <details
          key={i}
          open={!trace.done && streaming}
          className="group/trace overflow-hidden rounded-lg border border-border-soft bg-surface/40"
        >
          <summary className="flex cursor-pointer list-none items-center gap-xs px-sm py-[5px] text-xs text-text-subtle hover:text-text-muted [&::-webkit-details-marker]:hidden">
            <svg
              className="h-3 w-3 shrink-0 transition-transform group-open/trace:rotate-180"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
            <span>{TOOL_LABELS[trace.tool] ?? trace.tool}</span>
            {!trace.done && streaming && (
              <span className="ml-xs animate-pulse text-text-subtle">···</span>
            )}
            {trace.done && (
              <span className="ml-xs text-text-subtle/50">完成</span>
            )}
          </summary>
          <div className="border-t border-border-soft px-sm py-xs space-y-2xs">
            {trace.args && (
              <div className="text-xs text-text-subtle">
                <span className="text-text-subtle/60">参数 </span>
                <span className="font-mono">{trace.args}</span>
              </div>
            )}
            {trace.result && (
              <div className="text-xs text-text-subtle">
                <span className="text-text-subtle/60">结果 </span>
                <span>{trace.result}</span>
              </div>
            )}
          </div>
        </details>
      ))}
    </div>
  )
}
```

#### 改动②：`AssistantBubble` 接收并渲染 `TracePanel`

```tsx
function AssistantBubble({
  text,
  streaming,
  usage,
  traces,
}: {
  text: string
  streaming: boolean
  usage?: { input: number; output: number }
  traces?: import('../lib/chatSession').TraceEntry[]
}) {
  const segments = parseThinkSegments(text)

  return (
    <div className="ink-fade-in">
      <div className="mb-2xs text-xs text-text-subtle">学长</div>
      <div className="mb-sm h-px w-12 bg-border" />

      {traces && traces.length > 0 ? (
        <TracePanel traces={traces} streaming={streaming} />
      ) : null}

      {segments.map((seg, i) =>
        seg.kind === 'think' ? (
          <ThinkingCard key={i} content={seg.content} closed={seg.closed} />
        ) : (
          <div key={i} className="prose prose-sm max-w-none text-base">
            <ReactMarkdown>{seg.content}</ReactMarkdown>
            {streaming && i === segments.length - 1 && seg.content ? (
              <span className="ink-cursor" />
            ) : null}
          </div>
        ),
      )}

      {streaming && !text ? (
        <span className="text-text-muted ink-cursor">正在写...</span>
      ) : null}

      {usage && !streaming ? (
        <div className="mt-xs flex gap-xs text-[11px] text-text-subtle/50">
          <span>输入 {usage.input}</span>
          <span>·</span>
          <span>输出 {usage.output}</span>
          <span>token</span>
        </div>
      ) : null}
    </div>
  )
}
```

#### 改动③：消息渲染时传入 `traces`

```tsx
{messages.map((message, index) =>
  message.role === 'assistant' ? (
    <AssistantBubble
      key={index}
      text={message.content}
      streaming={streaming && index === messages.length - 1}
      usage={message.usage}
      traces={message.traces}
    />
  ) : (
    <UserBubble key={index} text={message.content} />
  ),
)}
```

---

## 不改动的内容

- `stream_chat()` SSE 函数（保留，不动）
- `_sse_token`、`_sse_error`、`_sse_done` 辅助函数（保留）
- `chat_service.py` 的 `finally` 块（DB 保存、投影、后台记忆审查）完全保留
- `api.ts`（SSE 相关类型，不改）
- `InputBox`、`UserBubble`、`ThinkingCard` 不变
- `loadConversation`、`startNew` 不变
- `thinkSegments.ts` 不变
- `wsClient.ts` 的重连、心跳逻辑不变

---

## 验收标准

1. 发一条消息，AI 调用 `memory_search` 时，AssistantBubble 上方出现"搜索记忆"折叠卡片，展开时显示参数和结果
2. 工具调用进行中：卡片展开 + "···" 动画；工具完成：卡片折叠 + 显示"完成"
3. 多个工具调用依次展示（如先 `memory_search` 再 `memory_save`）
4. 流结束后 trace 面板保留（可手动展开查看）
5. 没有工具调用的简单回复（如"你好"）不显示 trace 面板
6. token 用量和 trace 面板共存，不冲突

---

## 注意事项

1. `run_stream_events()` 是**普通 async for 循环**，不是 `async with` 上下文管理器，不要加 `async with`
2. `event_kind` 是字符串字面量，用 `event.event_kind == 'function_tool_call'` 判断，不用 isinstance
3. `FunctionToolResultEvent.content` 可以是 `str | Sequence | None`，只取 `str` 情况，其他情况用空字符串兜底
4. `result` 事件里 `tool` 字段传空字符串（后端不知道对应哪个工具），前端靠"最后一个未完成的 trace"匹配
5. `cancelled` 检查放在 `async for event in` 循环顶部（与原来在 `stream_text` 循环里的位置对应）
6. 传输层已是 WebSocket（yield dict），不需要 `_sse_trace()` 辅助函数
