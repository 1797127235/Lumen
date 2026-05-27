import { createSimpleContext } from "./helper"
import { createGlobalEmitter } from "@solid-primitives/event-bus"
import { batch, onCleanup } from "solid-js"
import * as LumenApi from "@tui/lumen/api"

export type LumenEvent =
  | { type: "session.created"; sessionID: string; realConvID: string }
  | { type: "session.deleted"; sessionID: string }
  | { type: "message.user"; sessionID: string; messageID: string; text: string }
  | { type: "token"; sessionID: string; token: string }
  | { type: "thinking"; sessionID: string; delta: string }
  | { type: "trace"; sessionID: string; kind: string; tool: string; content: string }
  | { type: "response.done"; sessionID: string; convID: string; fullText: string }
  | { type: "response.error"; sessionID: string; message: string }
  | { type: "session.aborted"; sessionID: string }

// tempSessionID → real Lumen conversation_id
const sessionIDMap = new Map<string, string>()
// reverse map
const convIDToSessionID = new Map<string, string>()
// per-session abort controllers
const sessionAborts = new Map<string, AbortController>()

export const { use: useSDK, provider: SDKProvider } = createSimpleContext({
  name: "SDK",
  init: () => {
    const emitter = createGlobalEmitter<{ event: LumenEvent }>()

    function emit(event: LumenEvent) {
      emitter.emit("event", event)
    }

    onCleanup(() => {
      for (const ctrl of sessionAborts.values()) {
        ctrl.abort()
      }
    })

    const client = {
      session: {
        async create(_opts: {
          workspace?: string
          agent?: string
          model?: { providerID?: string; id?: string; variant?: string }
        }) {
          // Lumen doesn't need a pre-create — we return a temp ID
          const tempID = `tmp_${Date.now()}_${Math.random().toString(36).slice(2)}`
          return { data: { id: tempID }, error: null }
        },

        async prompt(opts: {
          sessionID: string
          parts: Array<{ type: string; text?: string; file?: string; [k: string]: unknown }>
          messageID?: string
          agent?: string
          model?: { providerID?: string; modelID?: string; id?: string }
          variant?: string
        }) {
          const { sessionID, parts } = opts
          const text = parts
            .filter((p) => p.type === "text")
            .map((p) => p.text ?? "")
            .join("")

          if (!text.trim()) return

          const realConvID = sessionIDMap.get(sessionID)
          const abort = new AbortController()
          sessionAborts.set(sessionID, abort)

          const userMsgID = `umsg_${Date.now()}`
          emit({ type: "message.user", sessionID, messageID: userMsgID, text })

          try {
            await LumenApi.sendMessage({
              message: text,
              conversationId: realConvID,
              signal: abort.signal,
              onToken(token) {
                emit({ type: "token", sessionID, token })
              },
              onThinking(delta) {
                emit({ type: "thinking", sessionID, delta })
              },
              onTrace(kind, tool, content) {
                emit({ type: "trace", sessionID, kind, tool, content })
              },
              onDone(convID, fullText) {
                if (!sessionIDMap.has(sessionID)) {
                  sessionIDMap.set(sessionID, convID)
                  convIDToSessionID.set(convID, sessionID)
                  emit({ type: "session.created", sessionID, realConvID: convID })
                }
                emit({ type: "response.done", sessionID, convID, fullText })
              },
              onError(msg) {
                emit({ type: "response.error", sessionID, message: msg })
              },
            })
          } finally {
            sessionAborts.delete(sessionID)
          }
        },

        async abort(opts: { sessionID: string }) {
          const ctrl = sessionAborts.get(opts.sessionID)
          if (ctrl) {
            ctrl.abort()
            sessionAborts.delete(opts.sessionID)
          }
          emit({ type: "session.aborted", sessionID: opts.sessionID })
        },

        async shell(_opts: unknown) {
          // shell mode not supported in Lumen
        },

        async command(_opts: unknown) {
          // slash commands not supported in Lumen
        },

        async delete(opts: { sessionID: string }) {
          const convID = sessionIDMap.get(opts.sessionID)
          if (convID) {
            await LumenApi.deleteConversation(convID)
            sessionIDMap.delete(opts.sessionID)
            convIDToSessionID.delete(convID)
          }
          emit({ type: "session.deleted", sessionID: opts.sessionID })
        },

        async fork(_opts: unknown) {
          return { data: undefined, error: new Error("Fork not supported") }
        },

        async list() {
          return { data: [], error: null }
        },

        async update(_opts: unknown) {
          return { data: undefined, error: null }
        },

        async revert(_opts: unknown) {
          return { data: undefined, error: null }
        },

        get(sessionID: string) {
          return sessionIDMap.get(sessionID)
        },
      },

      // Stub out other SDK namespaces that might be referenced
      global: {
        async upgrade(_opts: unknown) {
          return { data: undefined, error: new Error("Upgrade not supported") }
        },
        event(_opts?: unknown) {
          // Return an async iterable that never yields (no global SSE in Lumen)
          return {
            stream: (async function* () {})(),
          }
        },
      },

      find: {
        async files(_opts: { directory: string; query: string; limit?: number }) {
          return { data: [], error: null }
        },
      },

      app: {
        async skills() {
          return { data: [], error: null }
        },
      },

      auth: {
        async set(_opts: unknown) {
          return { data: undefined, error: null }
        },
      },

      permission: {
        async reply(_opts: unknown) {
          return { data: undefined, error: null }
        },
      },

      question: {
        async reply(_opts: unknown) {
          return { data: undefined, error: null }
        },
        async reject(_opts: unknown) {
          return { data: undefined, error: null }
        },
      },

      mcp: {
        async status() {
          return { data: {}, error: null }
        },
      },

      vcs: {
        async status(_opts?: unknown) {
          return { data: undefined, error: null }
        },
      },

      instance: {
        async dispose() {},
      },

      provider: {
        oauth: {
          async authorize(_opts: unknown) {
            return { data: undefined, error: null }
          },
          async callback(_opts: unknown) {
            return { data: undefined, error: null }
          },
        },
      },

      experimental: {
        workspace: {
          async list() {
            return { data: [] }
          },
          async status() {
            return { data: [] }
          },
          async remove(_opts: unknown) {
            return { data: undefined, error: null }
          },
          async syncList() {
            return { data: [], error: null }
          },
        },
        console: {
          async listOrgs() {
            return { data: [], error: null }
          },
          async switchOrg(_opts: unknown) {
            return { data: undefined, error: null }
          },
        },
      },

      path: {
        async get() {
          return { data: undefined }
        },
      },

      project: {
        async current() {
          return { data: undefined }
        },
      },

      sync: {
        async start() {},
      },

      v2: {
        session: {
          async messages(_opts: unknown) {
            return { data: [], error: null }
          },
        },
      },
    }

    return {
      client,
      event: emitter,
      url: LumenApi.BASE_URL,
      directory: process.cwd(),
      // expose helpers for sync to use
      getConvID: (sessionID: string) => sessionIDMap.get(sessionID),
      registerMapping: (sessionID: string, convID: string) => {
        sessionIDMap.set(sessionID, convID)
        convIDToSessionID.set(convID, sessionID)
      },
    }
  },
})
