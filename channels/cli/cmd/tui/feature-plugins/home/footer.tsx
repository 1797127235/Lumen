import type { TuiPlugin, TuiPluginApi } from "@opencode-ai/plugin/tui"
import type { InternalTuiPlugin } from "../../plugin/internal"
import { createMemo, Match, Show, Switch } from "solid-js"
import { Global } from "@opencode-ai/core/global"

const id = "internal:home-footer"

function Directory(props: { api: TuiPluginApi }) {
  const theme = () => props.api.theme.current
  const pathParts = createMemo(() => {
    const dir = props.api.state.path.directory || process.cwd()
    // Normalize Windows backslashes
    const normalizedDir = dir.replace(/\\/g, "/")
    const out = normalizedDir.replace(Global.Path.home, "~")
    const branch = props.api.state.vcs?.branch
    const full = branch ? out + ":" + branch : out
    // Skip Windows drive letter colon (e.g. "E:")
    const searchStart = /^[A-Za-z]:/.test(full) ? 2 : 0
    const colonIdx = full.indexOf(":", searchStart)
    let pathPart = full
    let branchPart = ""
    if (colonIdx > 0) {
      pathPart = full.slice(0, colonIdx)
      branchPart = full.slice(colonIdx + 1)
    }
    const segs = pathPart.split("/")
    return {
      parent: segs.length > 1 ? segs.slice(0, -1).join("/") + "/" : "",
      name: segs.at(-1) ?? "",
      branch: branchPart,
    }
  })

  return (
    <box flexDirection="row">
      <Show when={pathParts().parent}>
        <text fg={theme().textMuted}>{pathParts().parent}</text>
      </Show>
      <text fg={theme().text}>{pathParts().name}</text>
      <Show when={pathParts().branch}>
        <text fg={theme().textMuted}>:</text>
        <text fg={theme().primary}>{pathParts().branch}</text>
      </Show>
    </box>
  )
}

function Mcp(props: { api: TuiPluginApi }) {
  const theme = () => props.api.theme.current
  const list = createMemo(() => props.api.state.mcp())
  const has = createMemo(() => list().length > 0)
  const err = createMemo(() => list().some((item) => item.status === "failed"))
  const count = createMemo(() => list().filter((item) => item.status === "connected").length)

  return (
    <Show when={has()}>
      <box gap={1} flexDirection="row" flexShrink={0}>
        <text fg={theme().text}>
          <Switch>
            <Match when={err()}>
              <span style={{ fg: theme().error }}>⊙ </span>
            </Match>
            <Match when={true}>
              <span style={{ fg: count() > 0 ? theme().success : theme().textMuted }}>⊙ </span>
            </Match>
          </Switch>
          {count()} MCP
        </text>
        <text fg={theme().textMuted}>/status</text>
      </box>
    </Show>
  )
}

function Version(props: { api: TuiPluginApi }) {
  const theme = () => props.api.theme.current

  return (
    <box flexShrink={0}>
      <text fg={theme().textMuted}>{props.api.app.version}</text>
    </box>
  )
}

function View(props: { api: TuiPluginApi }) {
  return (
    <box
      width="100%"
      paddingTop={1}
      paddingBottom={1}
      paddingLeft={2}
      paddingRight={2}
      flexDirection="row"
      flexShrink={0}
      gap={2}
    >
      <Directory api={props.api} />
      <Mcp api={props.api} />
      <box flexGrow={1} />
      <Version api={props.api} />
    </box>
  )
}

const tui: TuiPlugin = async (api) => {
  api.slots.register({
    order: 100,
    slots: {
      home_footer() {
        return <View api={api} />
      },
    },
  })
}

const plugin: InternalTuiPlugin = {
  id,
  tui,
}

export default plugin
