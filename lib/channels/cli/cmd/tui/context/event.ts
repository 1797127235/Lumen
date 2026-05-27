// Standalone event emitter — replaces the SDK-bridging implementation.
// In Lumen there are no plugins emitting TuiEvents, so subscriptions are no-ops
// that don't crash the UI components that call event.on(...).

type Handler = (event: { type: string; properties: Record<string, unknown> }, meta: { workspace: undefined }) => void

const listeners = new Map<string, Set<Handler>>()

function subscribe(handler: Handler) {
  const allKey = "*"
  let set = listeners.get(allKey)
  if (!set) {
    set = new Set()
    listeners.set(allKey, set)
  }
  set.add(handler)
  return () => set!.delete(handler)
}

function on<T extends string>(
  type: T,
  handler: (event: { type: T; properties: Record<string, unknown> }, meta: { workspace: undefined }) => void,
) {
  return subscribe((event, meta) => {
    if (event.type !== type) return
    handler(event as { type: T; properties: Record<string, unknown> }, meta)
  })
}

function emit(type: string, properties: Record<string, unknown> = {}) {
  const set = listeners.get("*")
  if (!set) return
  for (const handler of set) {
    handler({ type, properties }, { workspace: undefined })
  }
}

export function useEvent() {
  return { subscribe, on, emit }
}
