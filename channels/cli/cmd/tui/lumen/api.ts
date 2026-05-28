const PORT = process.env.LUMEN_PORT ?? "8000"
export const BASE_URL = `http://127.0.0.1:${PORT}/api`

export type LumenConversation = {
  conversation_id: string
  title?: string
  message_count: number
  total_tokens: number
  last_message_at?: string
  created_at: string
}

export type LumenMessage = {
  message_id: string
  role: "user" | "assistant"
  content?: string
  intent?: string
  tokens_used?: number
  created_at: string
}

export type SseEvent =
  | { type: "token"; content: string }
  | { type: "thinking"; content: string }
  | { type: "trace"; kind: string; tool: string; content: string }
  | { type: "done"; content: string; conversation_id: string }
  | { type: "error"; message: string }

export type LumenConfig = {
  llm_provider: string
  llm_model: string
  llm_base_url: string
  has_llm_key: boolean
  context_window?: number
}

export async function getConfig(): Promise<LumenConfig> {
  const res = await fetch(`${BASE_URL}/config`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function listConversations(userId = "demo_user", limit = 50): Promise<LumenConversation[]> {
  const res = await fetch(`${BASE_URL}/chat/history?user_id=${userId}&limit=${limit}`)
  if (!res.ok) throw new Error(`Failed to list conversations: ${res.status}`)
  return res.json()
}

export async function getConversationMessages(
  conversationId: string,
  userId = "demo_user",
): Promise<LumenMessage[]> {
  const res = await fetch(`${BASE_URL}/chat/${conversationId}?user_id=${userId}`)
  if (!res.ok) throw new Error(`Failed to get messages: ${res.status}`)
  return res.json()
}

export async function deleteConversation(conversationId: string, userId = "demo_user"): Promise<void> {
  const res = await fetch(`${BASE_URL}/chat/${conversationId}?user_id=${userId}`, { method: "DELETE" })
  if (!res.ok) throw new Error(`Failed to delete conversation: ${res.status}`)
}

export async function renameConversation(
  conversationId: string,
  title: string,
  userId = "demo_user",
): Promise<void> {
  const res = await fetch(`${BASE_URL}/chat/${conversationId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, user_id: userId }),
  })
  if (!res.ok) throw new Error(`Failed to rename conversation: ${res.status}`)
}

export interface SendOptions {
  message: string
  conversationId?: string
  userId?: string
  onToken(token: string): void
  onThinking(delta: string): void
  onTrace(kind: string, tool: string, content: string): void
  onDone(conversationId: string, content: string): void
  onError(message: string): void
  signal?: AbortSignal
}

export async function sendMessage(opts: SendOptions): Promise<void> {
  const { message, conversationId, userId = "demo_user", signal } = opts

  const res = await fetch(`${BASE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: conversationId ?? null, user_id: userId }),
    signal,
  })

  if (!res.ok || !res.body) {
    opts.onError(`HTTP ${res.status}`)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ""

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buf += decoder.decode(value, { stream: true })
      const lines = buf.split("\n")
      buf = lines.pop() ?? ""

      for (const line of lines) {
        if (!line.startsWith("data:")) continue
        const json = line.slice(5).trim()
        if (!json) continue
        try {
          const evt = JSON.parse(json) as SseEvent
          switch (evt.type) {
            case "token":
              opts.onToken(evt.content)
              break
            case "thinking":
              opts.onThinking(evt.content)
              break
            case "trace":
              opts.onTrace(evt.kind, evt.tool, evt.content)
              break
            case "done":
              opts.onDone(evt.conversation_id, evt.content)
              break
            case "error":
              opts.onError(evt.message)
              break
          }
        } catch {
          // skip malformed JSON
        }
      }
    }
  } catch (err) {
    if (signal?.aborted) return
    opts.onError(String(err))
  } finally {
    reader.releaseLock()
  }
}

export async function getTUICommands(): Promise<
  Array<{ name: string; description: string; arg_required: boolean }>
> {
  const res = await fetch(`${BASE_URL}/commands/list`)
  if (!res.ok) throw new Error(`获取命令列表失败: ${res.statusText}`)
  return res.json()
}

export async function executeCommand(
  command: string,
  args: string,
  sessionID?: string,
): Promise<{
  ok: boolean
  action?: string
  session_id?: string
  title?: string
  text?: string
  response?: string
  error?: string
}> {
  const res = await fetch(`${BASE_URL}/commands/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command, arguments: args, session_id: sessionID }),
  })
  if (!res.ok) throw new Error(`执行命令失败: ${res.statusText}`)
  return res.json()
}
