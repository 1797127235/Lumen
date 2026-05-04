import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { chatStream, getConversation } from './api'

export type ChatMessage = { role: 'user' | 'assistant'; content: string }

type ChatSessionValue = {
  messages: ChatMessage[]
  streaming: boolean
  conversationId: string | null
  error: string | null
  sendMessage: (text: string) => Promise<void>
  loadConversation: (id: string) => Promise<void>
  startNew: () => void
}

const CHAT_CONV_STORAGE_KEY = 'career-os:chat-conversation-id'

const ChatSessionContext = createContext<ChatSessionValue | null>(null)

function readStoredConversationId(): string | null {
  try {
    return sessionStorage.getItem(CHAT_CONV_STORAGE_KEY)
  } catch {
    return null
  }
}

function writeStoredConversationId(id: string | null) {
  try {
    if (id) sessionStorage.setItem(CHAT_CONV_STORAGE_KEY, id)
    else sessionStorage.removeItem(CHAT_CONV_STORAGE_KEY)
  } catch {
    /* ignore storage failures */
  }
}

export function ChatSessionProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(() => readStoredConversationId())
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const loadingRef = useRef(false)

  useEffect(() => {
    writeStoredConversationId(conversationId)
  }, [conversationId])

  // 卸载时 abort 进行中的请求
  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  async function sendMessage(text: string) {
    const content = text.trim()
    if (!content || streaming || loadingRef.current) return

    setMessages((prev) => [
      ...prev,
      { role: 'user', content },
      { role: 'assistant', content: '' },
    ])
    setStreaming(true)
    setError(null)

    const ctrl = new AbortController()
    abortRef.current = ctrl
    let completed = false

    let nextConversationId = conversationId
    await chatStream(content, conversationId, {
      signal: ctrl.signal,
      onToken: (delta, cid) => {
        if (completed) return
        if (!nextConversationId) {
          nextConversationId = cid
          setConversationId(cid)
        }
        if (!delta) return
        setMessages((prev) => {
          const next = prev.slice()
          const last = next[next.length - 1]
          if (last && last.role === 'assistant') {
            next[next.length - 1] = { ...last, content: last.content + delta }
          }
          return next
        })
      },
      onDone: (cid) => {
        if (completed) return
        completed = true
        setConversationId(cid)
        setStreaming(false)
        abortRef.current = null
      },
      onError: (message) => {
        if (completed) return
        completed = true
        setStreaming(false)
        setError(message)
        abortRef.current = null
        // 移除末尾的空 assistant 消息
        setMessages((prev) => {
          const last = prev[prev.length - 1]
          if (last && last.role === 'assistant' && !last.content) {
            return prev.slice(0, -1)
          }
          return prev
        })
      },
    })
  }

  async function loadConversation(id: string) {
    abortRef.current?.abort()
    abortRef.current = null
    loadingRef.current = true
    setStreaming(false)
    setError(null)

    try {
      const items = await getConversation(id)
      setMessages(
        items
          .filter((item) => item.role === 'user' || item.role === 'assistant')
          .map((item) => ({
            role: item.role as 'user' | 'assistant',
            content: item.content ?? '',
          })),
      )
      setConversationId(id)
    } catch (e) {
      setError((e as Error).message || '加载会话失败')
      setConversationId(null)
      setMessages([])
    } finally {
      loadingRef.current = false
    }
  }

  function startNew() {
    abortRef.current?.abort()
    abortRef.current = null
    loadingRef.current = false
    setMessages([])
    setConversationId(null)
    setStreaming(false)
    setError(null)
  }

  return (
    <ChatSessionContext.Provider
      value={{
        messages,
        streaming,
        conversationId,
        error,
        sendMessage,
        loadConversation,
        startNew,
      }}
    >
      {children}
    </ChatSessionContext.Provider>
  )
}

export function useChatSession() {
  const value = useContext(ChatSessionContext)
  if (!value) {
    throw new Error('useChatSession must be used within ChatSessionProvider')
  }
  return value
}
