import { render, useTerminalDimensions } from "@opentui/solid"
import { createDefaultOpenTuiKeymap } from "@opentui/keymap/opentui"
import * as Clipboard from "@tui/util/clipboard"
import * as Selection from "@tui/util/selection"
import { createCliRenderer, MouseButton } from "@opentui/core"
import { RouteProvider, useRoute } from "@tui/context/route"
import { Switch, Match, createMemo, ErrorBoundary, createSignal, onMount, batch, Show } from "solid-js"
import { win32DisableProcessedInput, win32InstallCtrlCGuard } from "./win32"
import { DialogProvider, useDialog } from "@tui/ui/dialog"
import { ErrorComponent } from "@tui/component/error-component"
import { SDKProvider } from "@tui/context/sdk"
import { SyncProvider, SyncProviderV2 } from "@tui/context/sync"
import { LocalProvider } from "@tui/context/local"
import { ThemeProvider, useTheme } from "@tui/context/theme"
import { DialogThemeList } from "@tui/component/dialog-theme-list"
import { DialogHelp } from "./ui/dialog-help"
import { DialogSessionList } from "@tui/component/dialog-session-list"
import { DialogModel } from "@tui/component/dialog-model"
import { Home } from "@tui/routes/home"
import { Session } from "@tui/routes/session"
import { PromptHistoryProvider } from "./component/prompt/history"
import { PromptStashProvider } from "./component/prompt/stash"
import { DialogAlert } from "./ui/dialog-alert"
import { DialogConfirm } from "./ui/dialog-confirm"
import { ToastProvider, useToast } from "./ui/toast"
import { ExitProvider, useExit } from "./context/exit"
import { TuiEvent } from "./event"
import { KVProvider, useKV } from "./context/kv"
import { ProjectProvider } from "@tui/context/project"
import { PromptRefProvider, usePromptRef } from "./context/prompt"
import { TuiConfigProvider, useTuiConfig } from "./context/tui-config"
import { CommandPaletteProvider, useCommandPalette } from "./context/command-palette"
import { FrecencyProvider } from "./component/prompt/frecency"
import { OpencodeKeymapProvider, registerOpencodeKeymap, useBindings, useOpencodeKeymap } from "./keymap"
import { useEvent } from "@tui/context/event"
import { TuiConfig, createDefaultConfig } from "@/cli/cmd/tui/config/tui"
import { FormatError, FormatUnknownError } from "@/cli/error"
import { ArgsProvider } from "@tui/context/args"
import { EditorContextProvider } from "@tui/context/editor"
import semver from "semver"
import open from "open"

function errorMessage(error: unknown) {
  const formatted = FormatError(error)
  if (formatted !== undefined) return formatted
  if (
    typeof error === "object" &&
    error !== null &&
    "data" in error &&
    typeof (error as any).data === "object" &&
    (error as any).data !== null &&
    "message" in (error as any).data &&
    typeof (error as any).data.message === "string"
  ) {
    return (error as any).data.message
  }
  return FormatUnknownError(error)
}

export function tui() {
  return new Promise<void>(async (resolve) => {
    const unguard = win32InstallCtrlCGuard()
    win32DisableProcessedInput()

    const onExit = async () => {
      unguard?.()
      resolve()
    }
    const onBeforeExit = async () => {
      offKeymap()
    }

    const config = createDefaultConfig()

    const renderer = await createCliRenderer({
      externalOutputMode: "passthrough",
      targetFps: 60,
      gatherStats: false,
      exitOnCtrlC: false,
      useKittyKeyboard: {},
      autoFocus: false,
      openConsoleOnError: false,
      useMouse: config.mouse ?? true,
      consoleOptions: {
        keyBindings: [{ name: "y", ctrl: true, action: "copy-selection" }],
        onCopySelection: (text) => {
          Clipboard.copy(text).catch(console.error)
        },
      },
    })

    void renderer.getPalette({ size: 16 }).catch(() => undefined)
    const mode = (await renderer.waitForThemeMode(1000)) ?? "dark"

    const keymap = createDefaultOpenTuiKeymap(renderer)
    const offKeymap = registerOpencodeKeymap(keymap, renderer, config)

    await render(() => {
      return (
        <ErrorBoundary
          fallback={(error, reset) => (
            <ErrorComponent error={error} reset={reset} onBeforeExit={onBeforeExit} onExit={onExit} mode={mode} />
          )}
        >
          <OpencodeKeymapProvider keymap={keymap}>
            <ArgsProvider>
            <ExitProvider onBeforeExit={onBeforeExit} onExit={onExit}>
              <KVProvider>
                <ToastProvider>
                  <RouteProvider>
                    <TuiConfigProvider config={config}>
                      <SDKProvider>
                        <ProjectProvider>
                          <SyncProvider>
                            <SyncProviderV2>
                              <ThemeProvider mode={mode}>
                                <LocalProvider>
                                  <EditorContextProvider>
                                  <PromptStashProvider>
                                    <DialogProvider>
                                      <CommandPaletteProvider>
                                        <FrecencyProvider>
                                          <PromptHistoryProvider>
                                            <PromptRefProvider>
                                              <App />
                                            </PromptRefProvider>
                                          </PromptHistoryProvider>
                                        </FrecencyProvider>
                                      </CommandPaletteProvider>
                                    </DialogProvider>
                                  </PromptStashProvider>
                                  </EditorContextProvider>
                                </LocalProvider>
                              </ThemeProvider>
                            </SyncProviderV2>
                          </SyncProvider>
                        </ProjectProvider>
                      </SDKProvider>
                    </TuiConfigProvider>
                  </RouteProvider>
                </ToastProvider>
              </KVProvider>
            </ExitProvider>
            </ArgsProvider>
          </OpencodeKeymapProvider>
        </ErrorBoundary>
      )
    }, renderer)
  })
}

function App() {
  const tuiConfig = useTuiConfig()
  const route = useRoute()
  const dimensions = useTerminalDimensions()
  const dialog = useDialog()
  const kv = useKV()
  const command = useCommandPalette()
  const keymap = useOpencodeKeymap()
  const event = useEvent()
  const toast = useToast()
  const themeState = useTheme()
  const { theme, mode, setMode, locked, lock, unlock } = themeState
  const exit = useExit()
  const promptRef = usePromptRef()

  const [terminalTitleEnabled, setTerminalTitleEnabled] = createSignal(kv.get("terminal_title_enabled", true))

  const appCommands = createMemo(() => [
    {
      name: "session.list",
      title: "Switch conversation",
      category: "Session",
      namespace: "palette",
      run: () => {
        dialog.replace(() => <DialogSessionList />)
      },
    },
    {
      name: "session.new",
      title: "New conversation",
      category: "Session",
      namespace: "palette",
      run: () => {
        route.navigate({ type: "home" })
        dialog.clear()
      },
    },
    {
      name: "model.list",
      title: "Switch model",
      category: "Model",
      namespace: "palette",
      run: () => {
        dialog.replace(() => <DialogModel />)
      },
    },
    {
      name: "theme.switch",
      title: "Switch theme",
      category: "System",
      namespace: "palette",
      run: () => {
        dialog.replace(() => <DialogThemeList />)
      },
    },
    {
      name: "theme.switch_mode",
      title: mode() === "dark" ? "Switch to light mode" : "Switch to dark mode",
      category: "System",
      namespace: "palette",
      run: () => {
        setMode(mode() === "dark" ? "light" : "dark")
        dialog.clear()
      },
    },
    {
      name: "theme.mode.lock",
      title: locked() ? "Unlock theme mode" : "Lock theme mode",
      category: "System",
      namespace: "palette",
      run: () => {
        if (locked()) unlock()
        else lock()
        dialog.clear()
      },
    },
    {
      name: "help.show",
      title: "Help",
      category: "System",
      namespace: "palette",
      run: () => {
        dialog.replace(() => <DialogHelp />)
      },
    },
    {
      name: "app.exit",
      title: "Exit",
      category: "System",
      namespace: "palette",
      run: () => exit(),
    },
    {
      name: "app.debug",
      title: "Toggle debug panel",
      category: "System",
      namespace: "palette",
      run: () => {
        // renderer not directly accessible here — handled by the renderer
      },
    },
    {
      name: "terminal.title.toggle",
      title: terminalTitleEnabled() ? "Disable terminal title" : "Enable terminal title",
      category: "System",
      namespace: "palette",
      run: () => {
        setTerminalTitleEnabled((prev) => {
          const next = !prev
          kv.set("terminal_title_enabled", next)
          return next
        })
        dialog.clear()
      },
    },
  ])

  useBindings(() => ({
    commands: appCommands(),
  }))

  useBindings(() => ({
    enabled: command.matcher,
    bindings: tuiConfig.keybinds.gather("app", [
      "command.palette.show",
      "session.list",
      "session.new",
      "theme.switch",
      "theme.switch_mode",
      "theme.mode.lock",
      "help.show",
    ]),
  }))

  useBindings(() => ({
    enabled: () => {
      const ok = command.matcher.get()
      if (!ok) return false
      const current = promptRef.current
      if (!current?.focused) return true
      return current.current.input === ""
    },
    bindings: tuiConfig.keybinds.gather("app_exit", ["app.exit"]),
  }))

  event.on(TuiEvent.CommandExecute.type, (evt) => {
    command.run(evt.properties.command)
  })

  event.on(TuiEvent.ToastShow.type, (evt) => {
    toast.show({
      title: evt.properties.title,
      message: evt.properties.message,
      variant: evt.properties.variant,
      duration: evt.properties.duration,
    })
  })

  event.on(TuiEvent.SessionSelect.type, (evt) => {
    route.navigate({ type: "session", sessionID: evt.properties.sessionID })
  })

  return (
    <box
      width={dimensions().width}
      height={dimensions().height}
      flexDirection="column"
      backgroundColor={theme.background}
    >
      <box flexGrow={1} minHeight={0} flexDirection="column">
        <Switch>
          <Match when={route.data.type === "home"}>
            <Home />
          </Match>
          <Match when={route.data.type === "session"}>
            <Session />
          </Match>
        </Switch>
      </box>
    </box>
  )
}
