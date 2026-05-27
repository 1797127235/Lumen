# Lumen CLI TUI — TypeScript 实现方案

## 背景

原 `lib/channels/cli.py`（prompt_toolkit Python TUI）已删除。  
目标：用与 opencode 相同的技术栈（`@opentui/solid` + SolidJS）重写 CLI，  
视觉和交互对齐 opencode，数据层换成 Lumen 自己的 FastAPI 后端。

参考源码：`E:\OpenHub\opencode\packages\opencode\src\cli\cmd\tui\`

---

## 技术栈

| 依赖 | 用途 |
|------|------|
| `@opentui/core` | 终端渲染引擎（Zig native + TS bindings），已在 npm 公开发布 |
| `@opentui/solid` | SolidJS → terminal 绑定（`useTerminalDimensions` 等） |
| `@opentui/keymap` | 快捷键系统 |
| `solid-js` | 响应式 UI 框架 |
| `fuzzysort` | 会话列表模糊搜索 |
| `bun` | 运行时（opencode 用 bun，建议对齐） |

---

## 目录结构

```
lib/channels/cli/
  package.json
  tsconfig.json
  src/
    index.ts                  # 入口：连接后端，启动 TUI
    api.ts                    # Lumen HTTP API client
    app.tsx                   # 根组件：管理 chat / session-list 两个视图
    context/
      theme.tsx               # 主题色（从 opencode context/theme/ 裁剪）
      session.tsx             # 会话状态（当前 conversation_id、列表缓存）
    components/
      session-list.tsx        # 会话选择器（改自 dialog-session-list.tsx）
      dialog-select.tsx       # 通用列表组件（改自 ui/dialog-select.tsx）
      chat.tsx                # 聊天主界面（消息历史 + 流式输出）
      input.tsx               # 底部输入框
      status.tsx              # 状态栏（模型名 / ctx token / 计时）
      spinner.tsx             # 思考 spinner（从 component/spinner.tsx 直接抄）
    keybind.ts                # 快捷键定义（从 config/keybind.ts 裁剪）
    theme.ts                  # 默认主题色值
```

---

## 可以直接从 opencode 复制的文件

以下文件依赖少，可以几乎原样复制，只改 import 路径：

| opencode 文件 | 目标路径 | 改动 |
|---------------|----------|------|
| `component/spinner.tsx` | `src/components/spinner.tsx` | 只改 import |
| `config/keybind.ts` | `src/keybind.ts` | 删掉 workspace/MCP/provider 相关条目，保留 session_* |
| `context/theme/opencode.json` | `src/themes/opencode.json` | 直接复制 |
| `ui/dialog-select.tsx` | `src/components/dialog-select.tsx` | 见下方改动说明 |
| `component/dialog-session-list.tsx` | `src/components/session-list.tsx` | 见下方改动说明 |

---

## 需要自己写的文件

### `package.json`

```json
{
  "name": "lumen-cli",
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "bun run src/index.ts",
    "build": "bun build src/index.ts --outfile dist/cli.js"
  },
  "dependencies": {
    "@opentui/core": "latest",
    "@opentui/solid": "latest",
    "@opentui/keymap": "latest",
    "solid-js": "^1.9.0",
    "fuzzysort": "^3.1.0"
  },
  "devDependencies": {
    "typescript": "^5.0.0",
    "@types/bun": "latest",
    "babel-preset-solid": "latest"
  }
}
```

---

### `src/api.ts` — Lumen API client

后端运行在 `http://localhost:8000`（由 `core/config.py` 的 `port` 配置决定，默认 8000）。

```typescript
const BASE = process.env.LUMEN_API ?? "http://localhost:8000/api"

export interface Conversation {
  conversation_id: string
  title: string | null
  message_count: number
  last_message_at: string | null
  created_at: string
}

export interface Message {
  message_id: string
  role: "user" | "assistant"
  content: string | null
  created_at: string
}

// 获取会话列表
export async function listConversations(limit = 50): Promise<Conversation[]> {
  const res = await fetch(`${BASE}/chat/history?user_id=demo_user&limit=${limit}`)
  return res.json()
}

// 获取某会话的消息列表
export async function getMessages(conversationId: string): Promise<Message[]> {
  const res = await fetch(`${BASE}/chat/${conversationId}?user_id=demo_user`)
  return res.json()
}

// 删除会话
export async function deleteConversation(conversationId: string): Promise<void> {
  await fetch(`${BASE}/chat/${conversationId}?user_id=demo_user`, { method: "DELETE" })
}

// 重命名会话（后端暂无此接口，需补充 PATCH /api/chat/{id}，见"后端补充"章节）
export async function renameConversation(conversationId: string, title: string): Promise<void> {
  await fetch(`${BASE}/chat/${conversationId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, user_id: "demo_user" }),
  })
}

// Pin/Unpin（后端暂无此接口，需补充 PATCH /api/chat/{id}，同上）
export async function pinConversation(conversationId: string, pinned: boolean): Promise<void> {
  await fetch(`${BASE}/chat/${conversationId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_pinned: pinned, user_id: "demo_user" }),
  })
}

// 发送消息，返回 SSE 流
// SSE 事件格式见 lib/channels/web.py 的 _stream_events 方法
export function sendMessage(
  message: string,
  conversationId: string | null,
  onDelta: (text: string) => void,
  onThinking: (text: string) => void,
  onToolCall: (name: string, args: string) => void,
  onDone: (usage: { input?: number }) => void,
  onError: (msg: string) => void,
): () => void {
  const controller = new AbortController()

  fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: conversationId, user_id: "demo_user" }),
    signal: controller.signal,
  }).then(async (res) => {
    const reader = res.body!.getReader()
    const decoder = new TextDecoder()
    let buf = ""

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split("\n")
      buf = lines.pop() ?? ""
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue
        try {
          const event = JSON.parse(line.slice(6))
          if (event.type === "delta") onDelta(event.content ?? "")
          else if (event.type === "thinking") onThinking(event.content ?? "")
          else if (event.type === "tool_call") onToolCall(event.name ?? "", event.args ?? "")
          else if (event.type === "done") onDone(event.usage ?? {})
          else if (event.type === "error") onError(event.message ?? "error")
        } catch {}
      }
    }
  }).catch((err) => {
    if (err.name !== "AbortError") onError(String(err))
  })

  return () => controller.abort()
}
```

> **注意**：需要先确认 `lib/channels/web.py` 的 SSE 事件格式，  
> 特别是 `type` 字段的值（`delta` / `thinking` / `tool_call` / `done`）。  
> 在 `web.py` 的 `_on_stream_delta` 和 `_on_response` 方法里确认。

---

### `src/context/session.tsx` — 会话状态

```typescript
import { createSignal } from "solid-js"
import type { Conversation } from "../api"

const [currentId, setCurrentId] = createSignal<string | null>(null)
const [conversations, setConversations] = createSignal<Conversation[]>([])
const [pinned, setPinned] = createSignal<string[]>([])

export const session = {
  currentId,
  setCurrentId,
  conversations,
  setConversations,
  pinned,
  togglePin: (id: string) => {
    setPinned((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    )
  },
}
```

---

## 改动说明：`dialog-select.tsx`

原文件 `E:\OpenHub\opencode\packages\opencode\src\cli\cmd\tui\ui\dialog-select.tsx`

**需要替换的 import：**

| 原 import | 替换为 |
|-----------|--------|
| `useDialog` from `@tui/ui/dialog` | 自己实现一个简单的 dialog context，或直接用 prop 回调 |
| `useTheme` from `@tui/context/theme` | `import { theme } from "../theme"` |
| `useTerminalDimensions` from `@opentui/solid` | 保留，直接用 |
| `useBindings`, `useKeymapSelector` from `../keymap` | 保留，来自 `@opentui/keymap` |
| `formatKeyBindings` from `../keymap` | 保留 |
| `getScrollAcceleration` from `../util/scroll` | 简化：直接写死 `1` |
| `useTuiConfig` | 删掉，用 hardcoded keybinds |
| `Locale` from `@/util/locale` | 替换为本地实现（见下） |

**`Locale` 替换：**
```typescript
export const Locale = {
  time: (timestamp: number) => new Date(timestamp).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
  truncate: (str: string, len: number) => str.length > len ? str.slice(0, len - 1) + "…" : str,
}
```

---

## 改动说明：`session-list.tsx`（改自 `dialog-session-list.tsx`）

**删掉的部分：**
- workspace 相关逻辑（`workspaceID`、`Flag.OPENCODE_EXPERIMENTAL_WORKSPACES`、`recover()`、`warpWorkspaceSession`）
- `useRoute`、`useProject`、`useToast`、`useSync`
- `quickSwitch` 相关（`local.session.slots()`）
- `searchResults` 的 API 调用（换成本地 filter）

**保留/替换的部分：**

```typescript
// 原来：sdk.client.session.list(...)
// 替换为：从 session context 里读本地缓存的 conversations

// 原来：sdk.client.session.delete(...)
// 替换为：import { deleteConversation } from "../api"

// 原来：local.session.pinned()
// 替换为：session.pinned()

// 原来：local.session.togglePin(id)
// 替换为：session.togglePin(id)

// 原来：route.navigate({ type: "session", sessionID })
// 替换为：session.setCurrentId(id); dialog 回调关闭
```

**时间格式：**
```typescript
// 原来：Locale.time(x.time.updated)
// 替换为：new Date(x.last_message_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })

// 分组日期：
// 原来：new Date(x.time.updated).toDateString()  → "Mon May 26 2026"
// 替换为：new Date(x.last_message_at).toLocaleDateString("zh-CN", { month: "long", day: "numeric" })
// 或保持英文格式对齐 opencode 风格
```

**数据结构映射：**

| opencode session 字段 | Lumen Conversation 字段 |
|----------------------|------------------------|
| `x.id` | `x.conversation_id` |
| `x.title` | `x.title ?? "未命名会话"` |
| `x.time.updated` | `new Date(x.last_message_at).getTime()` |
| `x.parentID` | 不存在，跳过该过滤 |

---

## `src/app.tsx` — 根组件

```typescript
// 两个模式：
// - "chat"：显示聊天界面（messages + input + status bar）
// - "sessions"：显示会话选择器（全屏替换，不是浮层）
//
// 切换：Ctrl+L 打开 sessions，Enter 选择后回到 chat，Esc 取消

import { createSignal } from "solid-js"
import { Chat } from "./components/chat"
import { SessionList } from "./components/session-list"
import { session } from "./context/session"

export function App() {
  const [mode, setMode] = createSignal<"chat" | "sessions">("chat")
  
  // Ctrl+L → 打开 sessions
  // ...keybinding setup...
  
  return mode() === "sessions"
    ? <SessionList onClose={() => setMode("chat")} />
    : <Chat onOpenSessions={() => setMode("sessions")} />
}
```

---

## `src/index.ts` — 入口

```typescript
import { render } from "@opentui/solid"
import { App } from "./app"
import { listConversations } from "./api"
import { session } from "./context/session"

// 启动时拉取会话列表
const conversations = await listConversations()
session.setConversations(conversations)

// 渲染 TUI
render(() => <App />, document.body) // opentui 有自己的 render 挂载点，参考 opentui 文档
```

---

## 需要补充的后端接口

在 `E:\MyHub\Lumen\server\routes\chat.py` 里补充：

```python
class ConversationUpdate(BaseModel):
    title: str | None = None
    is_pinned: bool | None = None
    user_id: str = "demo_user"

@router.patch("/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    req: ConversationUpdate,
    db: AsyncSession = Depends(get_db),
):
    conv = await db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.user_id != req.user_id:
        raise HTTPException(status_code=403, detail="无权修改")
    if req.title is not None:
        conv.title = req.title
    if req.is_pinned is not None:
        conv.is_pinned = req.is_pinned
    await db.commit()
    return {"ok": True}
```

---

## 核心交互流程

```
启动
  └─ 拉取 conversations → session.setConversations()
  └─ 进入 Chat 视图（无活跃会话则显示空）

用户输入 → Enter
  └─ sendMessage(text, currentId())
  └─ SSE 流：onDelta → 追加到消息列表
  └─ onDone → 更新 token 统计，刷新 conversations 列表

Ctrl+L → 切到 Sessions 视图
  ├─ 显示会话列表（按日期分组）
  ├─ 输入过滤（fuzzysort）
  ├─ ↑↓ 导航，Enter 选择 → setCurrentId() → 回 Chat
  ├─ Ctrl+D 两步删除：第一次高亮变红，第二次调 deleteConversation()
  ├─ Ctrl+F pin/unpin → 调 pinConversation()
  ├─ Ctrl+R 重命名 → 弹出 rename 输入框 → 调 renameConversation()
  └─ Esc → 回 Chat

/new 命令 → setCurrentId(null) → 下一条消息自动创建新会话
```

---

## 验证 SSE 格式

在写 `api.ts` 的 `sendMessage` 之前，先确认 `lib/channels/web.py` 里 SSE 事件的实际 `type` 字段值。  
查看 `_on_stream_delta`、`_on_tool_call`、`_on_response` 方法的 `yield` 内容。

---

## 运行方式

```bash
# 先启动 Python 后端
cd E:\MyHub\Lumen
python main.py

# 另开终端，启动 CLI TUI
cd E:\MyHub\Lumen\lib\channels\cli
bun install
bun run dev
```

---

## 参考文件位置

| 文件 | 路径 |
|------|------|
| opencode session list | `E:\OpenHub\opencode\packages\opencode\src\cli\cmd\tui\component\dialog-session-list.tsx` |
| opencode dialog select | `E:\OpenHub\opencode\packages\opencode\src\cli\cmd\tui\ui\dialog-select.tsx` |
| opencode keybind | `E:\OpenHub\opencode\packages\opencode\src\cli\cmd\tui\config\keybind.ts` |
| opencode spinner | `E:\OpenHub\opencode\packages\opencode\src\cli\cmd\tui\component\spinner.tsx` |
| opencode theme | `E:\OpenHub\opencode\packages\opencode\src\cli\cmd\tui\context\theme/` |
| Lumen chat routes | `E:\MyHub\Lumen\server\routes\chat.py` |
| Lumen web channel (SSE 格式) | `E:\MyHub\Lumen\lib\channels\web.py` |
| Lumen chat models | `E:\MyHub\Lumen\lib\chat\models.py` |
