import { createMemo, Match, onCleanup, onMount, Show, Switch } from "solid-js"
import { useTheme } from "../../context/theme"
import { useSync } from "../../context/sync"
import { useLocal } from "../../context/local"
import { useConnected } from "../../component/use-connected"
import { createStore } from "solid-js/store"
import { useRoute } from "../../context/route"


const BAR_WIDTH = 10

function renderBar(pct: number): string {
  const clamped = Math.max(0, Math.min(100, pct))
  const filled = Math.round((clamped / 100) * BAR_WIDTH)
  return "█".repeat(filled) + "░".repeat(BAR_WIDTH - filled)
}

export function Footer() {
  const { theme } = useTheme()
  const sync = useSync()
  const local = useLocal()
  const route = useRoute()

  const sessionID = createMemo(() => {
    if (route.data.type !== "session") return ""
    return route.data.sessionID
  })

  const messages = createMemo(() => {
    const id = sessionID()
    if (!id) return []
    return sync.data.message[id] ?? []
  })

  const modelName = createMemo(() => {
    const parsed = local.model.parsed()
    return parsed.model || "Lumen"
  })

  const ctxUsage = createMemo(() => {
    const msg = messages()

    // Accumulate tokens_used from ALL assistant messages
    let tokens = 0
    for (const m of msg) {
      if (m.role === "assistant") {
        tokens += (m as any).tokens_used ?? 0
      }
    }

    const limit = local.model.contextWindow()
    const pct = limit > 0 ? Math.round((tokens / limit) * 100) : 0

    return {
      tokens,
      limit,
      pct,
      bar: renderBar(pct),
      hasLimit: limit > 0,
    }
  })

  const mcp = createMemo(() =>
    Object.values(sync.data.mcp).filter((x) => x.status === "connected").length,
  )
  const mcpError = createMemo(() =>
    Object.values(sync.data.mcp).some((x) => x.status === "failed"),
  )
  const permissions = createMemo(() => {
    if (route.data.type !== "session") return []
    return sync.data.permission[route.data.sessionID] ?? []
  })
  const connected = useConnected()

  const [store, setStore] = createStore({
    welcome: false,
  })

  onMount(() => {
    const timeouts: ReturnType<typeof setTimeout>[] = []

    function tick() {
      if (connected()) return
      if (!store.welcome) {
        setStore("welcome", true)
        timeouts.push(setTimeout(() => tick(), 5000))
        return
      }
      if (store.welcome) {
        setStore("welcome", false)
        timeouts.push(setTimeout(() => tick(), 10_000))
        return
      }
    }
    timeouts.push(setTimeout(() => tick(), 10_000))

    onCleanup(() => {
      timeouts.forEach(clearTimeout)
    })
  })

  const formatTokens = (n: number) => {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
    if (n >= 1_000) return `${Math.round(n / 1_000)}k`
    return `${n}`
  }

  return (
    <box flexDirection="row" justifyContent="space-between" gap={1} flexShrink={0}>
      {/* Left: context usage bar (always visible) */}
      <box flexDirection="row" gap={1}>
        <text fg={theme.text}>{modelName()}</text>
        <text fg={theme.textMuted}>
          {ctxUsage().hasLimit
            ? `${formatTokens(ctxUsage().tokens)}/${formatTokens(ctxUsage().limit)}`
            : ctxUsage().tokens > 0
              ? formatTokens(ctxUsage().tokens)
              : "0/0"}
        </text>
        <text
          fg={
            ctxUsage().pct > 90
              ? theme.error
              : ctxUsage().pct > 70
                ? theme.warning
                : theme.success
          }
        >
          [{ctxUsage().bar}]
        </text>
        <text fg={theme.textMuted}>{ctxUsage().pct}%</text>
      </box>

      {/* Right: status indicators */}
      <box gap={2} flexDirection="row" flexShrink={0}>
        <Switch>
          <Match when={store.welcome}>
            <text fg={theme.text}>
              Get started <span style={{ fg: theme.textMuted }}>/connect</span>
            </text>
          </Match>
          <Match when={connected()}>
            <Show when={permissions().length > 0}>
              <text fg={theme.warning}>
                <span style={{ fg: theme.warning }}>△</span>{" "}
                {permissions().length} Permission{permissions().length > 1 ? "s" : ""}
              </text>
            </Show>
            <Show when={mcp()}>
              <text fg={theme.text}>
                <Switch>
                  <Match when={mcpError()}>
                    <span style={{ fg: theme.error }}>⊙ </span>
                  </Match>
                  <Match when={true}>
                    <span style={{ fg: theme.success }}>⊙ </span>
                  </Match>
                </Switch>
                {mcp()} MCP
              </text>
            </Show>
            <text fg={theme.textMuted}>/status</text>
          </Match>
        </Switch>
      </box>
    </box>
  )
}
