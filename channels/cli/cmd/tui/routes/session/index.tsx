import { createEffect, createMemo, For, Match, on, onMount, Show, Switch } from "solid-js"
import { useTerminalDimensions, useRenderer } from "@opentui/solid"
import { MouseButton, TextAttributes } from "@opentui/core"
import * as Selection from "@tui/util/selection"
import { useRoute } from "@tui/context/route"
import { useSync } from "@tui/context/sync"
import { useSDK } from "@tui/context/sdk"
import { useTheme } from "@tui/context/theme"
import { Prompt, type PromptRef } from "@tui/component/prompt"
import { Toast, useToast } from "@tui/ui/toast"
import { usePromptRef } from "@tui/context/prompt"
import { useBindings } from "@tui/keymap"
import { useTuiConfig } from "@tui/context/tui-config"
import type { Message, Part } from "@opencode-ai/sdk/v2"
import { Footer } from "./footer"

// ─── inner components ────────────────────────────────────────────────────────

function UserMessage(props: { message: Message; parts: Part[] }) {
  const { theme } = useTheme()
  const text = () =>
    props.parts
      .filter((p) => p.type === "text")
      .map((p) => (p as any).text ?? "")
      .join("") || " "

  return (
    <box flexDirection="column" paddingBottom={1}>
      <box
        border={["left"]}
        borderColor={theme.primary}
        paddingLeft={2}
        paddingTop={1}
        paddingBottom={1}
        backgroundColor={theme.backgroundPanel}
      >
        <text fg={theme.primary} attributes={TextAttributes.BOLD}>
          You
        </text>
        <text fg={theme.text} wrapMode="word" flexGrow={1}>
          {text()}
        </text>
        <Show when={(props.message as any).error}>
          <text fg={theme.error}>⚠ {(props.message as any).error?.message}</text>
        </Show>
      </box>
    </box>
  )
}

function AssistantMessage(props: { message: Message; parts: Part[] }) {
  const { theme, syntax } = useTheme()

  const textParts = () => props.parts.filter((p) => p.type === "text")
  const toolParts = () => props.parts.filter((p) => p.type === "tool-invocation")
  const thinkingParts = () => props.parts.filter((p) => p.type === "thinking")

  const hasContent = () =>
    props.parts.some((p) => {
      if (p.type === "text") return (p as any).text?.length > 0
      if (p.type === "thinking") return (p as any).thinking?.length > 0
      return true
    })

  return (
    <Show when={hasContent()}>
      <box flexDirection="column" paddingBottom={1}>
        <text fg={theme.accent} attributes={TextAttributes.BOLD} paddingLeft={1}>
          Lumen
        </text>
        <For each={thinkingParts()}>
          {(part) => (
            <box paddingLeft={2} paddingBottom={1}>
              <text fg={theme.textMuted} attributes={TextAttributes.ITALIC}>
                {(part as any).thinking ?? ""}
              </text>
            </box>
          )}
        </For>
        <For each={toolParts()}>
          {(part) => {
            const inv = () => (part as any).toolInvocation ?? {}
            const status = () => inv().state ?? "pending"
            return (
              <box
                paddingLeft={2}
                paddingBottom={1}
                border={["left"]}
                borderColor={theme.border}
              >
                <text fg={theme.text}>
                  <span style={{ fg: theme.textMuted }}>⚙ </span>
                  <b>{inv().toolName ?? "tool"}</b>
                  <span style={{ fg: theme.textMuted }}> {status()}</span>
                </text>
              </box>
            )
          }}
        </For>
        <For each={textParts()}>
          {(part) => {
            const text = () => ((part as any).text ?? "").trim()
            return (
              <Show when={text()}>
                <box paddingLeft={2} flexShrink={0}>
                  <markdown
                    syntaxStyle={syntax()}
                    streaming={true}
                    internalBlockMode="top-level"
                    content={text()}
                    tableOptions={{ style: "grid" }}
                    fg={theme.text}
                    bg={theme.background}
                  />
                </box>
              </Show>
            )
          }}
        </For>
        <Show when={(props.message as any).error}>
          <box paddingLeft={2}>
            <text fg={theme.error}>⚠ {(props.message as any).error?.message}</text>
          </box>
        </Show>
      </box>
    </Show>
  )
}

// ─── Session page ─────────────────────────────────────────────────────────────

export function Session() {
  const route = useRoute()
  const sync = useSync()
  const sdk = useSDK()
  const { theme } = useTheme()
  const dimensions = useTerminalDimensions()
  const promptRef = usePromptRef()
  const tuiConfig = useTuiConfig()
  const renderer = useRenderer()
  const toast = useToast()

  const sessionID = createMemo(() => {
    if (route.data.type !== "session") return ""
    return route.data.sessionID
  })

  const messages = createMemo(() => {
    const id = sessionID()
    if (!id) return []
    return sync.data.message[id] ?? []
  })

  const isRunning = createMemo(() => {
    const id = sessionID()
    if (!id) return false
    return (sync.data.session_status[id] as any)?.status === "running"
  })

  // Show the "···" indicator only while running AND no assistant content visible yet
  const showSpinner = createMemo(() => {
    if (!isRunning()) return false
    const id = sessionID()
    if (!id) return true
    const msgs = sync.data.message[id] ?? []
    const lastAsst = [...msgs].reverse().find((m) => m.role === "assistant")
    if (!lastAsst) return true
    const parts = (sync.data.part[lastAsst.id] ?? (lastAsst as any).parts ?? []) as Part[]
    return !parts.some(
      (p) =>
        (p.type === "text" && (p as any).text?.length > 0) ||
        (p.type === "thinking" && (p as any).thinking?.length > 0),
    )
  })

  onMount(() => {
    const id = sessionID()
    if (id) void sync.session.sync(id)
  })

  createEffect(
    on(sessionID, (id) => {
      if (id) void sync.session.sync(id)
    }),
  )

  const bind = (r: PromptRef | undefined) => {
    promptRef.set(r)
  }

  useBindings(() => ({
    commands: [
      {
        name: "session.interrupt",
        title: "Interrupt",
        category: "Session",
        run() {
          const id = sessionID()
          if (id) void sdk.client.session.abort({ sessionID: id })
        },
      },
    ],
    bindings: tuiConfig.keybinds.gather("session", ["session.interrupt"]),
  }))

  return (
    <box
      flexDirection="row"
      height={dimensions().height}
      width={dimensions().width}
      onMouseDown={(evt: { button: number; preventDefault(): void; stopPropagation(): void }) => {
        if (evt.button !== MouseButton.RIGHT) return
        if (!Selection.copy(renderer, toast)) return
        evt.preventDefault()
        evt.stopPropagation()
      }}
    >
      {/* Main column */}
      <box flexDirection="column" flexGrow={1}>
        {/* Message list */}
        <scrollbox
          flexGrow={1}
          minHeight={0}
          paddingLeft={2}
          paddingRight={2}
          paddingTop={1}
          scrollbarOptions={{ visible: false }}
          stickyScroll={true}
          stickyStart="bottom"
          id="session-scroll"
        >
          <Show when={messages().length === 0}>
            <box paddingLeft={1} paddingTop={2}>
              <text fg={theme.textMuted}>Start the conversation below.</text>
            </box>
          </Show>
          <For each={messages()}>
            {(msg) => {
              // Read parts from sync.data.part (OpenCode-style separate store)
              const parts = () => (sync.data.part[msg.id] ?? (msg as any).parts ?? []) as Part[]
              return (
                <Switch>
                  <Match when={msg.role === "user"}>
                    <UserMessage message={msg} parts={parts()} />
                  </Match>
                  <Match when={msg.role === "assistant"}>
                    <AssistantMessage message={msg} parts={parts()} />
                  </Match>
                </Switch>
              )
            }}
          </For>
          <Show when={showSpinner()}>
            <box paddingLeft={1} paddingBottom={1}>
              <text fg={theme.accent} attributes={TextAttributes.BOLD}>
                Lumen{" "}
              </text>
              <text fg={theme.textMuted}>···</text>
            </box>
          </Show>
        </scrollbox>

        {/* Context bar: path + status */}
        <box
          paddingLeft={2}
          paddingRight={2}
          height={1}
          flexShrink={0}
          backgroundColor={theme.backgroundPanel}
        >
          <Footer />
        </box>

        {/* Input area */}
        <box flexShrink={0} paddingLeft={2} paddingRight={2} paddingTop={1}>
          <Prompt ref={bind} sessionID={sessionID()} disabled={isRunning()} />
        </box>
      </box>

      <Toast />
    </box>
  )
}
