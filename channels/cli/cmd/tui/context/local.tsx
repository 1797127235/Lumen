import { createStore } from "solid-js/store"
import { createSignal, onMount } from "solid-js"
import { createSimpleContext } from "./helper"
import * as LumenApi from "@tui/lumen/api"

// 仅作 config 拉取完成前的展示占位符——非真实 provider/model 名。
// 客户端不在发消息时回传模型（见 sdk.tsx），所以它永远不会到达后端/上游 API。
const DEFAULT_PROVIDER_ID = "lumen"
const DEFAULT_MODEL_ID = "lumen"

const LUMEN_AGENT = {
  name: "lumen",
  description: "Lumen AI Assistant",
  model: { providerID: DEFAULT_PROVIDER_ID, modelID: DEFAULT_MODEL_ID },
  mode: "default",
  hidden: false,
}

const DEFAULT_MODEL = {
  providerID: DEFAULT_PROVIDER_ID,
  modelID: DEFAULT_MODEL_ID,
}

export const { use: useLocal, provider: LocalProvider } = createSimpleContext({
  name: "Local",
  init: () => {
    const [ready] = createSignal(true)

    // Reactive model info — fetched from backend on mount
    const [modelInfo, setModelInfo] = createSignal({
      providerID: DEFAULT_PROVIDER_ID as string,
      modelID: DEFAULT_MODEL_ID as string,
      provider: "Lumen" as string,
      model: "Lumen" as string,
      contextWindow: 128_000 as number,
    })

    onMount(async () => {
      try {
        const cfg = await LumenApi.getConfig()
        const providerID = cfg.llm_provider
        const modelID = cfg.llm_model
        const provider = providerID.charAt(0).toUpperCase() + providerID.slice(1)
        console.log("[Local] Config fetched:", { providerID, modelID })
        setModelInfo({
          providerID,
          modelID,
          provider,
          model: modelID,
          contextWindow: cfg.context_window ?? 128_000,
        })
        // 等待 SolidJS 响应式更新生效
        await new Promise(resolve => setTimeout(resolve, 0))
        console.log("[Local] Model info updated, current:", modelInfo())
      } catch (err) {
        console.error("[Local] Failed to fetch config:", err)
        // backend not reachable — keep placeholder
      }
    })

    const [store, setStore] = createStore({
      agent: "lumen" as string,
      recentModels: [DEFAULT_MODEL] as typeof DEFAULT_MODEL[],
      favoriteModels: [] as typeof DEFAULT_MODEL[],
      mcpEnabled: {} as Record<string, boolean>,
      pinnedSessions: [] as string[],
      sessionSlots: [] as string[],
    })

    return {
      get ready() {
        return ready()
      },

      agent: {
        list() {
          return [LUMEN_AGENT]
        },
        current() {
          return LUMEN_AGENT
        },
        set(_name: string) {},
        move(_direction: 1 | -1) {},
        color(_name: string) {
          return undefined
        },
      },

      model: {
        get ready() {
          return true
        },
        current() {
          const { providerID, modelID } = modelInfo()
          return { providerID, modelID }
        },
        parsed() {
          const { providerID, modelID, provider, model } = modelInfo()
          return { providerID, modelID, provider, model }
        },
        contextWindow() {
          return modelInfo().contextWindow
        },
        recent() {
          return store.recentModels
        },
        favorite() {
          return store.favoriteModels
        },
        isFavorite(_model: typeof DEFAULT_MODEL) {
          return false
        },
        toggleFavorite(_model: typeof DEFAULT_MODEL) {},
        set(model: typeof DEFAULT_MODEL, _opts?: { recent?: boolean }) {
          setModelInfo(prev => ({
            ...prev,
            providerID: model.providerID,
            modelID: model.modelID,
            provider: model.providerID.charAt(0).toUpperCase() + model.providerID.slice(1),
            model: model.modelID,
          }))
        },
        cycle(_direction: 1 | -1) {},
        cycleFavorite(_direction: 1 | -1) {},
        variant: {
          current() {
            return undefined
          },
          selected() {
            return undefined
          },
          list() {
            return []
          },
          cycle() {},
          set(_v: string | undefined) {},
        },
      },

      mcp: {
        isEnabled(_name: string) {
          return store.mcpEnabled[_name] ?? true
        },
        toggle(_name: string) {
          setStore("mcpEnabled", _name, !store.mcpEnabled[_name])
        },
      },

      session: {
        pinned() {
          return store.pinnedSessions
        },
        togglePin(id: string) {
          const idx = store.pinnedSessions.indexOf(id)
          if (idx >= 0) {
            setStore("pinnedSessions", store.pinnedSessions.filter((x) => x !== id))
          } else {
            setStore("pinnedSessions", [...store.pinnedSessions, id])
          }
        },
        slots() {
          return store.sessionSlots
        },
        quickSwitch(_slot: number) {},
      },
    }
  },
})
