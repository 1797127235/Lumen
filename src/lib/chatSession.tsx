import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { chatStream, getChatHistory, getConversation } from './api'

function genId(): string {
  return crypto.randomUUID()
}

export type TraceEntry = {
  tool: string; args: string; result: string; done: boolean
  thinking?: string; duration?: string
}
export type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  usage?: { input: number; output: number }
  traces?: TraceEntry[]
  tokens_used?: number
}

export type AttachmentMeta = {
  path: string;
  name: string;
};

type ChatSessionValue = {
  messages: ChatMessage[]; streaming: boolean; conversationId: string | null; error: string | null
  sendMessage: (text: string) => Promise<void>; cancelStreaming: () => void
  loadConversation: (id: string) => Promise<void>; startNew: () => void
  attachments: AttachmentMeta[]
  addAttachment: (meta: AttachmentMeta) => void
  removeAttachment: (path: string) => void
  clearAttachments: () => void
}

const CHAT_CONV_STORAGE_KEY = 'lumen:chat-conversation-id'
const ChatSessionContext = createContext<ChatSessionValue | null>(null)

function stored(key: string): string | null {
  try { return sessionStorage.getItem(key) } catch { return null }
}
function store(key: string, val: string | null) {
  try { val ? sessionStorage.setItem(key, val) : sessionStorage.removeItem(key) } catch { /* */ }
}

type Background = { conversationId: string; messages: ChatMessage[]; streaming: boolean }

export function ChatSessionProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(() => stored(CHAT_CONV_STORAGE_KEY))
  const [error, setError] = useState<string | null>(null)
  const [attachments, setAttachments] = useState<AttachmentMeta[]>([])
  const abortRef = useRef<AbortController | null>(null)
  const bgRef = useRef<Background | null>(null)
  const didInitRef = useRef(false)

  useEffect(() => { store(CHAT_CONV_STORAGE_KEY, conversationId) }, [conversationId])

  // 组件挂载时，自动恢复最近的历史会话
  useEffect(() => {
    if (didInitRef.current) return
    didInitRef.current = true
    const cid = stored(CHAT_CONV_STORAGE_KEY)
    if (cid) {
      loadConversation(cid)
    } else {
      // sessionStorage 被清空（如关闭窗口后重新打开），尝试加载最近的历史会话
      getChatHistory(1)
        .then((items) => {
          if (items.length > 0 && items[0].conversation_id) {
            loadConversation(items[0].conversation_id)
          }
        })
        .catch((err) => {
          console.error('[ChatSession] getChatHistory failed:', err)
        })
    }
  }, [])

  // Route state updates to either React state (active) or background ref (detached stream)
  function apply(fn: (prev: ChatMessage[]) => ChatMessage[]) {
    if (bgRef.current) { bgRef.current.messages = fn(bgRef.current.messages) }
    else { setMessages(fn) }
  }

  const appendToken = useCallback((delta: string) =>
    (prev: ChatMessage[]) => {
      const next = prev.slice()
      const last = next[next.length - 1]
      if (last?.role === 'assistant') next[next.length - 1] = { ...last, content: last.content + delta }
      else next.push({ id: genId(), role: 'assistant' as const, content: delta })
      return next
    }, [])

  const appendTrace = useCallback((kind: 'call' | 'result', tool: string, content: string) =>
    (prev: ChatMessage[]) => {
      const next = prev.slice()
      const last = next[next.length - 1]
      if (!last || last.role !== 'assistant') {
        next.push({ id: genId(), role: 'assistant' as const, content: '', traces: [] })
      }
      const current = next[next.length - 1]
      const traces = current.traces ? [...current.traces] : []
      if (kind === 'call') traces.push({ tool, args: content, result: '', done: false })
      else { const idx = traces.map(t => t.done).lastIndexOf(false); if (idx >= 0) traces[idx] = { ...traces[idx], result: content, done: true } }
      next[next.length - 1] = { ...current, traces }
      return next
    }, [])

  const appendUsage = useCallback((usage: { input: number; output: number }) =>
    (prev: ChatMessage[]) => {
      const next = prev.slice()
      const last = next[next.length - 1]
      if (last?.role === 'assistant') next[next.length - 1] = { ...last, usage }
      return next
    }, [])

  const addAttachment = useCallback((meta: AttachmentMeta) => {
    setAttachments(prev => {
      if (prev.length >= 5) return prev;
      if (prev.some(a => a.path === meta.path)) return prev;
      return [...prev, meta];
    });
  }, []);

  const removeAttachment = useCallback((path: string) => {
    setAttachments(prev => prev.filter(a => a.path !== path));
  }, []);

  const clearAttachments = useCallback(() => {
    setAttachments([]);
  }, []);

  const sendMessage = useCallback(async function sendMessage(text: string) {
    const content = text.trim()
    if (!content || abortRef.current) return

    const ctrl = new AbortController()
    abortRef.current = ctrl
    const targetCid = conversationId

    // 附件随请求发出后立即清空 UI，不等 onDone
    clearAttachments()

    setMessages(prev => [...prev, { id: genId(), role: 'user', content }, { id: genId(), role: 'assistant', content: '' }])
    setStreaming(true)
    setError(null)

    try {
      await chatStream(content, targetCid, {
        signal: ctrl.signal,
        onToken: (delta, cid) => {
          if (ctrl.signal.aborted) return
          if (cid) setConversationId(prev => prev || cid)
          if (delta) apply(appendToken(delta))
        },
        onDone: (_cid, usage) => {
          if (ctrl.signal.aborted) return
          if (bgRef.current) {
            bgRef.current.streaming = false
            if (usage) bgRef.current.messages = appendUsage(usage)(bgRef.current.messages)
            if (conversationId === bgRef.current.conversationId) {
              setMessages(bgRef.current.messages)
              setStreaming(false)
              setConversationId(bgRef.current.conversationId)
              bgRef.current = null
            }
          } else {
            setStreaming(false)
            if (usage) apply(appendUsage(usage))
          }
        },
        onTrace: (kind, tool, content) => {
          if (!ctrl.signal.aborted) apply(appendTrace(kind, tool, content))
        },
        onError: (msg) => {
          if (ctrl.signal.aborted) return
          if (bgRef.current) { bgRef.current.streaming = false }
          else {
            setStreaming(false)
            setError(msg)
            setMessages(prev => { const last = prev[prev.length - 1]; return last?.role === 'assistant' && !last.content ? prev.slice(0, -1) : prev })
          }
        },
      }, attachments.map(a => a.path))
    } catch (e) {
      if ((e as Error).name !== 'AbortError' && !bgRef.current) {
        setStreaming(false)
        setError('生成回复失败，请稍后重试')
      }
    } finally {
      if (abortRef.current === ctrl) abortRef.current = null
    }
  }, [conversationId, appendToken, appendTrace, appendUsage, attachments, clearAttachments])

  const cancelStreaming = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    bgRef.current = null
    setStreaming(false)
  }, [])

  async function loadConversation(id: string) {
    if (abortRef.current) return
    if (id === conversationId && messages.length > 0) return

    if (bgRef.current?.conversationId === id) {
      setMessages(bgRef.current.messages)
      setStreaming(bgRef.current.streaming)
      setConversationId(bgRef.current.conversationId)
      setError(null)
      setAttachments([])
      return
    }

    setStreaming(false)
    setError(null)
    setAttachments([])
    try {
      const items = await getConversation(id)
      setMessages(items.filter(i => i.role === 'user' || i.role === 'assistant').map(i => ({ id: genId(), role: i.role as 'user' | 'assistant', content: i.content ?? '', tokens_used: i.tokens_used ?? undefined })))
      setConversationId(id)
    } catch (e) {
      setError((e as Error).message || '加载会话失败')
      if (!messages.length) { setConversationId(null); setMessages([]) }
    }
  }

  function startNew() {
    if (abortRef.current && conversationId) {
      bgRef.current = { conversationId, messages: [], streaming: true }
      abortRef.current = null
    } else {
      abortRef.current?.abort()
      abortRef.current = null
      bgRef.current = null
    }
    setMessages(prev => { if (bgRef.current) bgRef.current.messages = prev; return [] })
    setConversationId(null)
    setStreaming(false)
    setError(null)
    setAttachments([])
  }

  return (
    <ChatSessionContext.Provider value={{ messages, streaming, conversationId, error, sendMessage, cancelStreaming, loadConversation, startNew, attachments, addAttachment, removeAttachment, clearAttachments }}>
      {children}
    </ChatSessionContext.Provider>
  )
}

export function useChatSession() {
  const c = useContext(ChatSessionContext)
  if (!c) throw new Error('useChatSession must be used within ChatSessionProvider')
  return c
}
