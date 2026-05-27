import { createStore, produce } from "solid-js/store"
import { createSimpleContext } from "./helper"
import { batch, createSignal, onMount } from "solid-js"
import { useSDK, type LumenEvent } from "@tui/context/sdk"
import * as LumenApi from "@tui/lumen/api"
import type { Session, Message, Part, SessionStatus, Agent, Provider, Command, Config } from "@opencode-ai/sdk/v2"
import { emptyConsoleState } from "@/config/console-state"
import type { ConsoleState } from "@/config/console-state"
import type { Snapshot } from "@/snapshot"

// Lumen-compatible session shape (maps Lumen conversation → OpenCode Session)
function convToSession(conv: LumenApi.LumenConversation): Session {
  const updatedMs = conv.last_message_at ? new Date(conv.last_message_at).getTime() : new Date(conv.created_at).getTime()
  const createdMs = new Date(conv.created_at).getTime()
  return {
    id: conv.conversation_id,
    title: conv.title ?? undefined,
    time: { created: createdMs, updated: updatedMs },
    path: { cwd: process.cwd() },
  } as Session
}

// Lumen message → OpenCode Message (simplified)
function buildUserMessage(sessionID: string, msgID: string, text: string): Message {
  return {
    id: msgID,
    role: "user",
    sessionID,
    parts: [{ id: `${msgID}_p0`, type: "text", text } as Part],
  } as Message
}

export const { use: useSync, provider: SyncProvider } = createSimpleContext({
  name: "Sync",
  init: () => {
    const sdk = useSDK()

    const [statusSignal, setStatusSignal] = createSignal<"loading" | "partial" | "complete">("loading")
    const [readySignal, setReadySignal] = createSignal(false)

    const [store, setStore] = createStore<{
      session: Session[]
      session_status: Record<string, SessionStatus>
      session_diff: Record<string, Snapshot.FileDiff[]>
      message: Record<string, Message[]>
      part: Record<string, Part[]>
      todo: Record<string, unknown[]>
      provider: Provider[]
      provider_default: Record<string, string>
      provider_next: { providers: Provider[]; all: Provider[]; connected: string[] }
      console_state: ConsoleState
      provider_auth: Record<string, unknown[]>
      agent: Agent[]
      command: Command[]
      permission: Record<string, unknown[]>
      question: Record<string, unknown[]>
      config: Config
      lsp: unknown[]
      mcp: Record<string, unknown>
      mcp_resource: Record<string, unknown>
      formatter: unknown[]
      vcs: undefined
    }>({
      session: [],
      session_status: {},
      session_diff: {},
      message: {},
      part: {},
      todo: {},
      provider: [
        // Lumen uses Claude — provide a stub provider so the UI doesn't show "no provider" warning
        {
          id: "anthropic",
          name: "Anthropic",
          env: [],
          models: {
            "claude-sonnet-4-6": {
              id: "claude-sonnet-4-6",
              name: "Claude Sonnet 4.6",
              context: 200000,
              cost: { input: 3, output: 15 },
            },
          },
        } as unknown as Provider,
      ],
      provider_default: { anthropic: "claude-sonnet-4-6" },
      provider_next: { providers: [], all: [] as Provider[], connected: [] as string[] },
      console_state: emptyConsoleState(),
      provider_auth: {},
      agent: [
        {
          name: "lumen",
          description: "Lumen AI",
          model: { providerID: "anthropic", modelID: "claude-sonnet-4-6" },
          mode: "default",
          hidden: false,
        } as unknown as Agent,
      ],
      command: [],
      permission: {},
      question: {},
      config: { reference: {}, experimental: {} } as unknown as Config,
      lsp: [],
      mcp: {},
      mcp_resource: {},
      formatter: [],
      vcs: undefined,
    })

    // Streaming state per session
    const streamingText = new Map<string, string>()
    const streamingMsgID = new Map<string, string>()

    function ensureMessages(sessionID: string) {
      if (!store.message[sessionID]) {
        setStore("message", sessionID, [])
      }
    }

    function setSessionStatus(sessionID: string, status: "running" | "idle") {
      setStore("session_status", sessionID, { status } as SessionStatus)
    }

    // Listen to SDK events (Lumen-native events from our sdk.tsx)
    sdk.event.on("event", (event: LumenEvent) => {
      batch(() => {
        switch (event.type) {
          case "session.created": {
            const { sessionID, realConvID } = event
            // Register the mapping so future calls use the real ID
            sdk.registerMapping(sessionID, realConvID)
            // Refresh the session list to pick up the new conversation
            void refreshSessions()
            break
          }

          case "session.deleted": {
            setStore(
              "session",
              store.session.filter((s) => s.id !== event.sessionID),
            )
            break
          }

          case "message.user": {
            const { sessionID, messageID, text } = event
            ensureMessages(sessionID)
            setSessionStatus(sessionID, "running")

            const asstMsgID = `amsg_${Date.now()}`
            streamingMsgID.set(sessionID, asstMsgID)
            streamingText.set(sessionID, "")

            // Add user message to messages store
            setStore(
              produce((s) => {
                s.message[sessionID].push(buildUserMessage(sessionID, messageID, text))
                // Add assistant message shell (parts go in store.part separately)
                s.message[sessionID].push({
                  id: asstMsgID,
                  role: "assistant",
                  sessionID,
                } as Message)
              }),
            )
            // Populate user parts
            setStore("part", messageID, [{ id: `${messageID}_p0`, type: "text", text } as Part])
            // Initialize empty streaming part for assistant
            setStore("part", asstMsgID, [{ id: `${asstMsgID}_p0`, type: "text", text: "" } as Part])
            break
          }

          case "token": {
            const { sessionID, token } = event
            const msgID = streamingMsgID.get(sessionID)
            if (!msgID) break
            const prev = streamingText.get(sessionID) ?? ""
            const next = prev + token
            streamingText.set(sessionID, next)
            // Direct path update — much more reliable for fine-grained SolidJS reactivity
            // than deep mutations via produce
            const parts = store.part[msgID]
            if (parts) {
              const partIdx = (parts as Part[]).findIndex((p) => p.type === "text")
              if (partIdx >= 0) {
                setStore("part", msgID, partIdx, "text" as any, next)
              }
            }
            break
          }

          case "thinking": {
            const { sessionID, delta } = event
            const msgID = streamingMsgID.get(sessionID)
            if (!msgID) break
            const currentParts = store.part[msgID]
            if (!currentParts) break
            const thinkingIdx = (currentParts as Part[]).findIndex((p) => p.type === "thinking")
            if (thinkingIdx < 0) {
              // First thinking delta — prepend ThinkingPart before TextPart
              const newParts: Part[] = [
                { id: `${msgID}_think`, type: "thinking", thinking: delta } as Part,
                ...(currentParts as Part[]),
              ]
              setStore("part", msgID, newParts)
            } else {
              const prev = (currentParts[thinkingIdx] as any).thinking ?? ""
              setStore("part", msgID, thinkingIdx, "thinking" as any, prev + delta)
            }
            break
          }

          case "trace": {
            // Could add a tool trace part — for now we just ignore
            break
          }

          case "response.done": {
            const { sessionID } = event
            setSessionStatus(sessionID, "idle")
            streamingText.delete(sessionID)
            streamingMsgID.delete(sessionID)
            // Refresh session list to update title/message count
            void refreshSessions()
            break
          }

          case "response.error": {
            const { sessionID, message } = event
            setSessionStatus(sessionID, "idle")
            const msgID = streamingMsgID.get(sessionID)
            if (msgID) {
              setStore(
                produce((s) => {
                  const msgs = s.message[sessionID]
                  if (!msgs) return
                  const msg = msgs.find((m) => m.id === msgID)
                  if (msg) msg.error = { message }
                }),
              )
            }
            streamingText.delete(sessionID)
            streamingMsgID.delete(sessionID)
            break
          }

          case "session.aborted": {
            setSessionStatus(event.sessionID, "idle")
            break
          }
        }
      })
    })

    async function refreshSessions() {
      try {
        const convs = await LumenApi.listConversations()
        setStore(
          "session",
          convs.map(convToSession),
        )
      } catch {
        // ignore — backend might not be running yet
      }
    }

    async function loadMessages(sessionID: string) {
      // sessionID might be a temp ID or a real conv ID
      const realConvID = sdk.getConvID(sessionID) ?? sessionID
      try {
        const messages = await LumenApi.getConversationMessages(realConvID)
        ensureMessages(sessionID)
        const msgList = messages.map((m) => ({
          id: m.message_id,
          role: m.role,
          sessionID,
        } as Message))
        setStore("message", sessionID, msgList)
        // Populate store.part for each loaded message
        for (const m of messages) {
          setStore("part", m.message_id, [{ id: `${m.message_id}_p0`, type: "text", text: m.content ?? "" } as Part])
        }
      } catch {
        // conversation might not exist yet
      }
    }

    onMount(async () => {
      await refreshSessions()
      setStatusSignal("complete")
      setReadySignal(true)
    })

    return {
      get status() {
        return statusSignal()
      },
      get ready() {
        return readySignal()
      },
      data: store,
      path: {
        directory: process.cwd(),
        worktree: process.cwd(),
      },
      set(key: string, value: unknown) {
        setStore(key as any, value)
      },
      async bootstrap(_opts?: { fatal?: boolean }) {
        await refreshSessions()
      },
      session: {
        get(id: string) {
          return store.session.find((s) => s.id === id)
        },
        query() {
          return store.session
        },
        async refresh() {
          await refreshSessions()
        },
        status(id: string) {
          return store.session_status[id]
        },
        async sync(id: string) {
          await loadMessages(id)
        },
        message: {
          async sync(_id: string) {},
        },
      },
    }
  },
})

// SyncProviderV2 stub (app.tsx references it)
export const SyncProviderV2 = (props: { children: unknown }) => props.children as any
