# opencode CLI 架构详解

> 基于 opencode v1.15.5 源码分析

---

## 一、技术栈

| 层 | 技术 | 用途 |
|---|---|---|
| 渲染引擎 | `@opentui/core` | 终端 UI 原语（Box、Textarea、ScrollBox） |
| 框架绑定 | `@opentui/solid` | SolidJS 绑定层（render、useRenderer） |
| 响应式框架 | `solid-js` | 响应式 UI（signal、store、context） |
| 快捷键 | `@opentui/keymap` | 快捷键注册与管理 |
| 后端 SDK | `@opencode-ai/sdk/v2` | HTTP 客户端 + SSE 事件流 |
| 事件总线 | `@solid-primitives/event-bus` | 进程内事件发布订阅 |

---

## 二、目录结构

```
cmd/tui/
├── app.tsx                    # 主入口：渲染器 + Provider 嵌套 + 路由
├── routes/
│   ├── home.tsx               # 首页：Logo + 输入框居中
│   └── session/
│       └── index.tsx          # 会话页：消息列表 + 输入框（2346 行）
├── component/
│   ├── prompt/
│   │   ├── index.tsx          # 核心输入组件（1812 行，最复杂）
│   │   ├── autocomplete.tsx   # 命令/文件补全
│   │   ├── history.tsx        # 输入历史
│   │   ├── stash.tsx          # 输入暂存
│   │   ├── frecency.tsx       # 频率+最近使用排序
│   │   ├── part.ts            # 消息部分处理
│   │   └── traits.ts          # 输入特性计算
│   ├── logo.tsx               # ASCII Logo
│   ├── border.tsx             # 自定义边框字符
│   ├── spinner.tsx            # 加载动画
│   ├── dialog-*.tsx           # 各种弹窗组件（15+ 个）
│   ├── todo-item.tsx          # Todo 展示
│   ├── error-component.tsx    # 错误展示
│   └── startup-loading.tsx    # 启动加载
├── context/                   # 全局状态管理（SolidJS Context）
│   ├── helper.tsx             # Context 创建工具
│   ├── route.tsx              # 路由状态
│   ├── sdk.tsx                # SDK 客户端 + SSE
│   ├── sync.tsx               # 核心数据同步（551 行）
│   ├── local.tsx              # 本地选择状态（510 行）
│   ├── theme.tsx              # 主题系统（1248 行）
│   ├── event.ts               # 事件分发
│   ├── kv.tsx                 # 键值持久化
│   ├── exit.tsx               # 退出流程
│   ├── editor.ts              # 编辑器集成
│   ├── project.tsx            # 项目/工作区
│   ├── command-palette.tsx    # 命令面板
│   ├── args.tsx               # CLI 参数
│   ├── prompt.tsx             # Prompt 引用
│   ├── thinking.ts            # 思考模式
│   ├── tui-config.tsx         # TUI 配置
│   └── theme/                 # 主题 JSON 文件（30+）
├── ui/                        # 通用 UI 组件
│   ├── dialog.tsx             # 弹窗系统
│   ├── toast.tsx              # Toast 通知
│   ├── spinner.ts             # 动画帧生成
│   └── dialog-*.tsx           # 各种弹窗
├── util/                      # 工具函数
│   ├── clipboard.ts           # 剪贴板
│   ├── selection.ts           # 文本选择
│   ├── scroll.ts              # 滚动加速
│   ├── audio.ts               # 音效
│   ├── model.ts               # 模型名解析
│   └── transcript.ts          # 会话导出
├── keymap.tsx                 # 快捷键注册
├── config/                    # 配置
│   ├── keybind.ts             # 快捷键定义
│   └── tui-migrate.ts         # 配置迁移
└── plugin/                    # 插件系统
    └── runtime.ts             # 插件运行时
```

---

## 三、核心数据流

```
┌─────────────────────────────────────────────────────────────────┐
│                     opencode 后端 (TypeScript + Bun)             │
│   session.create / session.prompt / session.abort / ...         │
└───────────────────────────────┬─────────────────────────────────┘
                                │ HTTP REST + SSE
                                ↓
┌─────────────────────────────────────────────────────────────────┐
│                         sdk.tsx                                 │
│   createOpencodeClient({ baseUrl })                             │
│   sdk.global.event() → SSE 长连接                               │
│   sdk.session.create() / sdk.session.prompt() → HTTP POST       │
└───────────────────────────────┬─────────────────────────────────┘
                                │ GlobalEvent
                                ↓
┌─────────────────────────────────────────────────────────────────┐
│                         sync.tsx                                │
│   监听 SSE 事件，更新 store：                                    │
│   - session.created → store.session[]                           │
│   - message.updated → store.message[sessionID][]                │
│   - message.part.updated → store.part[messageID][]              │
│   - session.status → store.session_status[sessionID]            │
│   - permission.asked → store.permission[sessionID][]            │
│   - todo.updated → store.todo[sessionID][]                      │
└───────────────────────────────┬─────────────────────────────────┘
                                │ SolidJS signal 自动更新
                ┌───────────────┼───────────────┐
                ↓               ↓               ↓
           home.tsx      session/index.tsx  prompt/index.tsx
           (首页)         (会话页)           (输入组件)
```

---

## 四、核心 Context 详解

### 4.1 helper.tsx — Context 创建工具

```typescript
export function createSimpleContext<T, Props>(input: {
  name: string
  init: ((input: Props) => T) | (() => T)
}) {
  const ctx = createContext<T>()
  return {
    provider: (props) => <ctx.Provider value={init(props)}>{props.children}</ctx.Provider>,
    use: () => useContext(ctx),
  }
}
```

所有 Context 都用这个工厂函数创建，保证类型安全和错误提示。

### 4.2 route.tsx — 路由系统

```typescript
type Route = HomeRoute | SessionRoute | PluginRoute

// HomeRoute:    { type: "home" }
// SessionRoute: { type: "session", sessionID: "xxx" }
// PluginRoute:  { type: "plugin", id: "xxx", data?: {} }

// 使用
const route = useRoute()
route.navigate({ type: "session", sessionID: "abc123" })
route.data  // 当前路由
```

用 SolidJS store 管理当前路由状态，`navigate()` 触发页面切换。

### 4.3 sdk.tsx — 后端连接

```typescript
// 创建 SDK 客户端
const sdk = createOpencodeClient({ baseUrl: "http://localhost:PORT" })

// SSE 事件流（实时推送）
const events = await sdk.global.event({ signal })
for await (const event of events.stream) {
  handleEvent(event)  // 批量处理，16ms 窗口
}

// HTTP 调用
await sdk.session.create({ agent, model })
await sdk.session.prompt({ sessionID, parts })
await sdk.session.abort({ sessionID })
```

**关键设计**：
- 事件批量处理：16ms 窗口内收集事件，一次性更新 store，避免频繁渲染
- 指数退避重连：断线后 1s → 2s → 4s → ... → 30s 重连
- AbortController：组件卸载时取消所有请求

### 4.4 sync.tsx — 核心数据仓库（**最重要**）

这是整个 TUI 的数据中枢，管理所有状态：

```typescript
const [store, setStore] = createStore({
  // 会话
  session: Session[],                    // 所有会话列表
  session_status: { [sessionID]: SessionStatus },  // 会话状态
  session_diff: { [sessionID]: FileDiff[] },       // 会话 diff
  
  // 消息
  message: { [sessionID]: Message[] },   // 每个会话的消息
  part: { [messageID]: Part[] },         // 每条消息的部分
  
  // 配置
  provider: Provider[],                  // LLM 提供商
  agent: Agent[],                        // Agent 列表
  config: Config,                        // 配置
  command: Command[],                    // 可用命令
  
  // 运行时
  permission: { [sessionID]: PermissionRequest[] },  // 权限请求
  question: { [sessionID]: QuestionRequest[] },      // 问题请求
  todo: { [sessionID]: Todo[] },                     // Todo
  
  // 状态
  status: "loading" | "partial" | "complete",
  lsp: LspStatus[],
  mcp: { [key]: McpStatus },
  vcs: VcsInfo,
})
```

**事件驱动更新**：
```typescript
event.subscribe((event) => {
  switch (event.type) {
    case "session.created":
      // 二分查找插入位置，保持有序
      setStore("session", produce(draft => draft.splice(index, 0, session)))
      break
    case "message.updated":
      // 更新或插入消息
      setStore("message", sessionID, index, reconcile(message))
      break
    case "message.part.updated":
      // 更新消息部分（文本、工具调用、思考）
      setStore("part", messageID, index, reconcile(part))
      break
    case "permission.asked":
      // 权限请求（需要用户确认）
      setStore("permission", sessionID, produce(draft => draft.push(request)))
      break
    // ... 20+ 种事件类型
  }
})
```

**数据结构**：
- `Session`: `{ id, title, parentID?, share?, revert?, workspaceID?, time: { created, updated } }`
- `Message`: `{ id, sessionID, role: "user"|"assistant", agent, model, tokens, time, error?, finish? }`
- `Part`: `{ id, messageID, type: "text"|"tool"|"reasoning"|"file"|"compaction", ... }`
  - TextPart: `{ type: "text", text, synthetic?, ignored? }`
  - ToolPart: `{ type: "tool", tool, state: { status, input, output } }`
  - ReasoningPart: `{ type: "reasoning", text, time: { start, end? } }`
  - FilePart: `{ type: "file", mime, filename, url }`

### 4.5 local.tsx — 本地选择状态

管理用户当前选择（不持久化到后端）：

```typescript
const local = useLocal()

// Agent 选择
local.agent.list()           // 可用 agent 列表
local.agent.current()        // 当前 agent
local.agent.set("build")     // 切换 agent
local.agent.move(1)          // 上一个/下一个
local.agent.color("build")   // agent 颜色

// Model 选择
local.model.current()        // 当前模型 { providerID, modelID }
local.model.set({ providerID, modelID })
local.model.cycle(1)         // 切换最近使用的模型
local.model.cycleFavorite(1) // 切换收藏的模型
local.model.parsed()         // { provider, model, reasoning }

// Session 快速切换
local.session.slots()        // 9 个快速槽位
local.session.togglePin(id)  // 固定/取消固定
local.session.quickSwitch(3) // 切换到第 3 个槽位

// MCP 管理
local.mcp.toggle("name")    // 启用/禁用 MCP
```

**持久化**：模型选择保存到 `~/.config/opencode/state/model.json`，session 槽位保存到 `session.json`。

### 4.6 theme.tsx — 主题系统

**30+ 内置主题**：
```
aura, ayu, catppuccin, catppuccin-frappe, catppuccin-macchiato,
cobalt2, cursor, dracula, everforest, flexoki, github, gruvbox,
kanagawa, material, matrix, mercury, monokai, nightowl, nord,
one-dark, osaka-jade, opencode, orng, lucent-orng, palenight,
rosepine, solarized, synthwave84, tokyonight, vercel, vesper,
zenburn, carbonfox
```

**主题结构**：
```json
{
  "$schema": "...",
  "defs": {
    "surface0": "#1e1e2e",
    "surface1": "#313244"
  },
  "theme": {
    "background": "#11111b",
    "backgroundPanel": "surface0",
    "backgroundElement": "surface1",
    "text": "#cdd6f4",
    "textMuted": "#a6adc8",
    "primary": "#89b4fa",
    "secondary": "#f5c2e7",
    "accent": "#94e2d5",
    "error": "#f38ba8",
    "warning": "#fab387",
    "success": "#a6e3a1",
    "border": "#45475a",
    "borderActive": "#585b70",
    "markdownHeading": "#89b4fa",
    "markdownCode": "#a6e3a1",
    "syntaxKeyword": "#cba6f7",
    "syntaxFunction": "#89b4fa",
    "syntaxString": "#a6e3a1",
    "syntaxNumber": "#fab387",
    "syntaxType": "#89dceb",
    // ... 50+ 颜色变量
  }
}
```

**颜色引用系统**：
```json
{
  "defs": { "surface0": "#1e1e2e" },
  "theme": {
    "backgroundPanel": "surface0",  // 引用 defs
    "backgroundElement": {          // 按模式区分
      "dark": "#313244",
      "light": "#eff1f5"
    }
  }
}
```

**resolveTheme()** 递归解析引用链，支持：
- 直接颜色值：`"#ff0000"`
- defs 引用：`"surface0"`
- theme 引用：`"background"`
- 模式区分：`{ dark: "...", light: "..." }`

**系统主题**：从终端读取 16 色调色板，自动生成主题。

### 4.7 event.ts — 事件分发

```typescript
const event = useEvent()

// 订阅特定事件
event.on("session.created", (evt) => {
  console.log(evt.properties.info.id)
})

// 订阅所有事件
event.subscribe((event, metadata) => {
  console.log(event.type, metadata.workspace)
})
```

包装 SDK 的 SSE 事件，按 `event.directory` 和 `event.project` 过滤。

---

## 五、核心组件详解

### 5.1 app.tsx — 启动流程

```typescript
export function tui(input: { url, args, config }) {
  return new Promise(async (resolve) => {
    // 1. 创建终端渲染器
    const renderer = await createCliRenderer({
      targetFps: 60,
      exitOnCtrlC: false,
      useKittyKeyboard: {},
      useMouse: true,
    })
    
    // 2. 初始化快捷键
    const keymap = createDefaultOpenTuiKeymap(renderer)
    
    // 3. 渲染根组件（嵌套 15+ 层 Provider）
    await render(() => (
      <ErrorBoundary>
        <OpencodeKeymapProvider keymap={keymap}>
          <ArgsProvider {...args}>
            <ExitProvider>
              <KVProvider>
                <ToastProvider>
                  <RouteProvider>
                    <SDKProvider url={url}>
                      <ProjectProvider>
                        <SyncProvider>
                          <ThemeProvider mode={mode}>
                            <LocalProvider>
                              <DialogProvider>
                                <CommandPaletteProvider>
                                  <App />
                                </CommandPaletteProvider>
                              </DialogProvider>
                            </LocalProvider>
                          </ThemeProvider>
                        </SyncProvider>
                      </ProjectProvider>
                    </SDKProvider>
                  </RouteProvider>
                </ToastProvider>
              </KVProvider>
            </ExitProvider>
          </ArgsProvider>
        </OpencodeKeymapProvider>
      </ErrorBoundary>
    ), renderer)
  })
}
```

**App 组件**：
```typescript
function App() {
  const route = useRoute()
  
  return (
    <box width={dimensions().width} height={dimensions().height}>
      <Switch>
        <Match when={route.data.type === "home"}>
          <Home />
        </Match>
        <Match when={route.data.type === "session"}>
          <Session />
        </Match>
      </Switch>
    </box>
  )
}
```

### 5.2 home.tsx — 首页

简洁的欢迎页：

```tsx
export function Home() {
  return (
    <>
      <box flexGrow={1} alignItems="center" paddingLeft={2} paddingRight={2}>
        <box flexGrow={1} minHeight={0} />        {/* 上方弹性空白 */}
        <box height={4} minHeight={0} flexShrink={1} />
        <Logo />                                    {/* ASCII Logo */}
        <box height={1} minHeight={0} flexShrink={1} />
        <box width="100%" maxWidth={75}>            {/* 输入框，最大宽度 75 */}
          <Prompt placeholders={placeholder} />
        </box>
        <box flexGrow={1} minHeight={0} />        {/* 下方弹性空白 */}
      </box>
    </>
  )
}
```

**布局**：Logo 居中，输入框居中（maxWidth=75），上下弹性空白撑开。

### 5.3 prompt/index.tsx — 输入组件（1812 行）

这是最复杂的组件，实现完整的输入体验：

#### 基础结构
```tsx
export function Prompt(props: PromptProps) {
  let input: TextareaRenderable
  
  return (
    <box ref={anchor}>
      <box border={["left"]} borderColor={highlight()}>
        <box paddingLeft={2} paddingRight={2} paddingTop={1} backgroundColor={theme.backgroundElement}>
          <textarea
            minHeight={1}
            maxHeight={6}
            placeholder={placeholderText()}
            onContentChange={() => { /* 同步到 store */ }}
            onSubmit={() => { /* 提交逻辑 */ }}
            onKeyDown={(e) => { /* 快捷键处理 */ }}
            onPaste={(event) => { /* 粘贴处理 */ }}
            ref={(r) => { input = r }}
          />
          <box flexDirection="row" justifyContent="space-between">
            <text>{agentName} · {modelName}</text>
            <text>{contextUsage}</text>
          </box>
        </box>
      </box>
    </box>
  )
}
```

#### 核心功能

**1. 提交逻辑**
```typescript
async function submit() {
  if (submitting) return  // 防重提交
  submitting = true
  
  const text = input.plainText.trim()
  if (!text) return
  
  // 1. 创建 session（如果是新对话）
  if (!sessionID) {
    const res = await sdk.client.session.create({ agent, model })
    sessionID = res.data.id
  }
  
  // 2. 发送消息
  await sdk.client.session.prompt({
    sessionID,
    parts: [{ type: "text", text }],
    agent, model, variant
  })
  
  // 3. 清空输入
  input.clear()
  setStore("prompt", { input: "", parts: [] })
  
  // 4. 导航到 session 页面
  if (!props.sessionID) {
    route.navigate({ type: "session", sessionID })
  }
}
```

**2. Shell 模式**
```typescript
// 输入 ! 切换到 shell 模式
if (text.startsWith("!")) {
  setStore("mode", "shell")
  // 执行 shell 命令
  await sdk.client.session.shell({ sessionID, command: text.slice(1) })
}
```

**3. 历史记录**
```typescript
const history = usePromptHistory()

// 上箭头：上一条历史
history.move(-1, currentInput)

// 下箭头：下一条历史
history.move(1, currentInput)

// 提交后保存到历史
history.append({ input, parts, mode })
```

**4. Stash（暂存）**
```typescript
const stash = usePromptStash()

// 暂存当前输入
stash.push({ input, parts })
input.clear()

// 恢复暂存
const entry = stash.pop()
input.setText(entry.input)
```

**5. 粘贴处理**
```typescript
async function pasteInputText(text: string) {
  const normalized = text.replace(/\r\n/g, "\n")
  
  // 长文本折叠
  const lineCount = (normalized.match(/\n/g)?.length ?? 0) + 1
  if (lineCount >= 3 || normalized.length > 150) {
    pasteText(normalized, `[Pasted ~${lineCount} lines]`)
    return
  }
  
  // 图片/PDF 作为附件
  if (mime.startsWith("image/") || mime === "application/pdf") {
    await pasteAttachment({ filename, mime, content })
    return
  }
  
  // 直接插入
  input.insertText(normalized)
}
```

**6. 快捷键绑定**
```typescript
useBindings({
  target: inputTarget,
  enabled: () => !props.disabled,
  bindings: tuiConfig.keybinds.get("prompt.paste"),  // Ctrl+V
})

useBindings({
  target: inputTarget,
  enabled: () => store.prompt.input !== "",
  bindings: tuiConfig.keybinds.get("prompt.clear"),  // Ctrl+U
})

useBindings({
  target: inputTarget,
  enabled: () => store.mode === "normal" && input.visualCursor.offset === 0,
  bindings: [{ key: "!", cmd: () => setStore("mode", "shell") }],
})
```

**7. IME 支持**
```typescript
onSubmit={() => {
  // 双重 setTimeout 等待 IME 组合字符刷新
  setTimeout(() => setTimeout(() => submit(), 0), 0)
}}
```

### 5.4 session/index.tsx — 会话页（2346 行）

#### 布局结构
```tsx
export function Session() {
  return (
    <box flexDirection="row" flexGrow={1}>
      {/* 左侧：消息列表 */}
      <box flexGrow={1} paddingLeft={2} paddingRight={2}>
        <scrollbox ref={scroll} stickyScroll={true} stickyStart="bottom">
          <For each={messages()}>
            {(message) => (
              <Switch>
                <Match when={message.role === "user"}>
                  <UserMessage message={message} parts={parts} />
                </Match>
                <Match when={message.role === "assistant"}>
                  <AssistantMessage message={message} parts={parts} />
                </Match>
              </Switch>
            )}
          </For>
        </scrollbox>
        
        {/* 底部：输入框 */}
        <Prompt sessionID={route.sessionID} />
      </box>
      
      {/* 右侧：Sidebar */}
      <Show when={sidebarVisible()}>
        <Sidebar sessionID={route.sessionID} />
      </Show>
    </box>
  )
}
```

#### UserMessage 组件
```tsx
function UserMessage(props) {
  return (
    <box border={["left"]} borderColor={color()} marginTop={1}>
      <box paddingLeft={2} paddingTop={1} paddingBottom={1} backgroundColor={hover() ? theme.backgroundElement : theme.backgroundPanel}>
        <text>{text()}</text>
        {/* 文件附件 */}
        <For each={files()}>
          {(file) => <text><span style={{ bg }}> {mime} </span> {filename}</text>}
        </For>
        {/* 时间戳 */}
        <Show when={showTimestamps()}>
          <text fg={theme.textMuted}>{formatTime(message.time.created)}</text>
        </Show>
      </box>
    </box>
  )
}
```

**样式**：
- 左边框，颜色由 agent 决定
- 鼠标悬停背景变深
- 点击弹出消息操作菜单

#### AssistantMessage 组件
```tsx
function AssistantMessage(props) {
  return (
    <>
      {/* 遍历所有 Part */}
      <For each={parts}>
        {(part) => (
          <Switch>
            <Match when={part.type === "text"}>
              <TextPart part={part} />
            </Match>
            <Match when={part.type === "tool"}>
              <ToolPart part={part} />
            </Match>
            <Match when={part.type === "reasoning"}>
              <ReasoningPart part={part} />
            </Match>
          </Switch>
        )}
      </For>
      
      {/* 错误信息 */}
      <Show when={message.error}>
        <box border={["left"]} borderColor={theme.error}>
          <text>{message.error.data.message}</text>
        </box>
      </Show>
      
      {/* 元信息：模型、耗时 */}
      <Show when={last || final()}>
        <text>
          ▣ {agentName} · {modelName} · {duration}
        </text>
      </Show>
    </>
  )
}
```

#### ToolPart 组件
```tsx
function ToolPart(props) {
  const part = props.part
  const status = part.state.status
  
  return (
    <box border={["left"]} borderColor={theme.border}>
      <box paddingLeft={2}>
        {/* 工具名 + 状态 */}
        <text>
          <span style={{ bold: true }}>{part.tool}</span>
          <span style={{ fg: theme.textMuted }}> {status}</span>
        </text>
        
        {/* 输入参数（折叠） */}
        <Show when={showDetails()}>
          <text fg={theme.textMuted}>{JSON.stringify(part.state.input)}</text>
        </Show>
        
        {/* 输出结果（折叠） */}
        <Show when={status === "completed" && showDetails()}>
          <text>{collapseToolOutput(part.state.output)}</text>
        </Show>
        
        {/* 加载动画 */}
        <Show when={status === "running"}>
          <Spinner />
        </Show>
      </box>
    </box>
  )
}
```

#### ReasoningPart 组件
```tsx
function ReasoningPart(props) {
  const [expanded, setExpanded] = createSignal(false)
  const inMinimal = () => thinkingMode() === "hide"
  
  return (
    <Show when={content()}>
      <box border={["left"]} borderColor={theme.borderSubtle}>
        <box paddingLeft={2}>
          {/* 标题行 */}
          <text fg={theme.textMuted}>
            <span style={{ bold: true }}>Thinking</span>
            <span> · {duration}</span>
            <span> · {expanded() ? "▼" : "▶"}</span>
          </text>
          
          {/* 内容（可折叠） */}
          <Show when={!inMinimal() || expanded()}>
            <text fg={theme.textMuted} style={{ opacity: thinkingOpacity }}>
              {content()}
            </text>
          </Show>
        </box>
      </box>
    </Show>
  )
}
```

---

## 六、快捷键系统

### 6.1 keymap.tsx

```typescript
// 注册全局快捷键
registerOpencodeKeymap(keymap, renderer, config)

// 在组件中使用
useBindings({
  commands: [
    { name: "session.share", title: "Share session", run: () => {} },
    { name: "session.rename", title: "Rename session", run: () => {} },
  ]
})

useBindings({
  enabled: command.matcher,  // 命令面板打开时
  bindings: tuiConfig.keybinds.gather("session", [
    "session.share",
    "session.rename",
    "session.timeline",
  ]),
})
```

### 6.2 默认快捷键

| 快捷键 | 命令 | 说明 |
|---|---|---|
| `Ctrl+K` | command.palette.show | 命令面板 |
| `Ctrl+L` | session.list | 会话列表 |
| `Ctrl+N` | session.new | 新建会话 |
| `Ctrl+C` | session.interrupt | 中断（按两次） |
| `Ctrl+E` | prompt.editor | 打开编辑器 |
| `Ctrl+V` | prompt.paste | 粘贴 |
| `Ctrl+U` | prompt.clear | 清空输入 |
| `Enter` | prompt.submit | 提交 |
| `Shift+Enter` | - | 换行 |
| `!` | - | Shell 模式（行首） |
| `Escape` | - | 退出 shell 模式 |

---

## 七、弹窗系统

### 7.1 dialog.tsx

```typescript
const dialog = useDialog()

// 压入弹窗
dialog.replace(() => <DialogSessionList />)

// 清空弹窗栈
dialog.clear()

// 弹窗栈
dialog.stack  // Dialog[]
```

### 7.2 常用弹窗

| 弹窗 | 触发 | 功能 |
|---|---|---|
| `DialogSessionList` | `Ctrl+L` | 会话列表，支持搜索 |
| `DialogModel` | `/models` | 模型切换 |
| `DialogAgent` | `/agents` | Agent 切换 |
| `DialogProvider` | `/connect` | Provider 连接 |
| `DialogHelp` | `/help` | 帮助信息 |
| `DialogThemeList` | `/themes` | 主题切换 |
| `DialogSessionRename` | `/rename` | 重命名会话 |
| `DialogTimeline` | `/timeline` | 消息时间线 |
| `DialogConfirm` | - | 确认对话框 |
| `DialogAlert` | - | 提示对话框 |

---

## 八、插件系统

### 8.1 plugin/runtime.ts

```typescript
TuiPluginRuntime.init({
  api: createTuiApi({ ... }),
  config: tuiConfig,
})

// 插槽渲染
<TuiPluginRuntime.Slot name="home_logo" mode="replace">
  <Logo />  {/* 默认内容 */}
</TuiPluginRuntime.Slot>
```

**可用插槽**：
- `home_logo` — 首页 Logo
- `home_prompt` — 首页输入框
- `home_prompt_right` — 输入框右侧
- `home_bottom` — 首页底部
- `home_footer` — 首页页脚
- `session_prompt` — 会话输入框
- `session_prompt_right` — 会话输入框右侧
- `app_bottom` — 应用底部
- `app` — 应用级插槽

---

## 九、与 Lumen 的差异

| 维度 | opencode | Lumen |
|---|---|---|
| 后端语言 | Go | Python (FastAPI) |
| 数据模型 | Session → Message → Part | Conversation → Message |
| Agent | 多 Agent（build/plan/subagent） | 单 Agent |
| Provider | 多 Provider（openai/anthropic/...） | 单 Provider（配置） |
| 权限系统 | PermissionRequest + QuestionRequest | 无 |
| 工具系统 | 丰富（read/write/shell/grep/...） | 4 个工具 |
| 工作区 | Workspace + Git | 无 |
| MCP | MCP 服务器管理 | 无 |
| 会话操作 | share/fork/compact/undo/redo | 基础 CRUD |
| 主题 | 30+ 内置 + 自定义 | 单一主题 |
| 插件 | 完整插件系统 | 无 |

---

## 十、适配 Lumen 的关键替换点

### 必须替换

| opencode 模块 | 替换方案 | 复杂度 |
|---|---|---|
| `sdk.tsx` | 自定义 HTTP 客户端调用 `/api/chat` | 中 |
| `sync.tsx` | 简化版，只保留 conversation/message | 高 |
| `local.tsx` | 简化，去掉 agent/provider/model | 中 |
| `event.ts` | 适配 Lumen SSE 事件格式 | 低 |

### 可以保留

| opencode 模块 | 说明 |
|---|---|
| `theme.tsx` | 主题系统是纯前端的 |
| `route.tsx` | 路由逻辑通用 |
| `kv.tsx` | 键值持久化通用 |
| `ui/*` | 弹窗、Toast、Spinner 通用 |
| `util/*` | 工具函数通用 |
| `component/logo.tsx` | Logo 组件 |
| `component/border.tsx` | 边框组件 |
| `keymap.tsx` | 快捷键框架 |

### 需要精简

| opencode 模块 | 精简内容 |
|---|---|
| `prompt/index.tsx` | 去掉 autocomplete、stash、shell、editor 集成 |
| `session/index.tsx` | 去掉 permission、sidebar、fork、undo/redo |

---

## 附录：依赖清单

```json
{
  "dependencies": {
    "@opentui/core": "catalog:",
    "@opentui/keymap": "catalog:",
    "@opentui/solid": "catalog:",
    "solid-js": "catalog:",
    "@opencode-ai/sdk": "workspace:*",
    "@solid-primitives/event-bus": "1.1.2",
    "opentui-spinner": "catalog:",
    "remeda": "catalog:",
    "strip-ansi": "7.1.2"
  }
}
```
