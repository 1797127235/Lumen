import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { getConversation } from './api'
import { ChatWS } from './wsClient'
import { getUserId } from './userId'

export type ChatMessage = {
  role: 'user' | 'assistant'
  content: string
  usage?: { input: number; output: number }
}

type ChatSessionValue = {
  messages: ChatMessage[]
  streaming: boolean
  conversationId: string | null
  error: string | null
  sendMessage: (text: string) => Promise<void>
  cancelStreaming: () => void
  loadConversation: (id: string) => Promise<void>
  startNew: () => void
}

const CHAT_CONV_STORAGE_KEY = 'lumen:chat-conversation-id'

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
  const [conversationId, setConversationId] = useState<string | null>(() =>
    readStoredConversationId(),
  )
  const [error, setError] = useState<string | null>(null)
  const loadingRef = useRef(false)
  const wsRef = useRef<ChatWS | null>(null)
  const completedRef = useRef(false)
  const nextConversationIdRef = useRef<string | null>(null)

  // 初始化 WebSocket
  useEffect(() => {
    const ws = new ChatWS({
      onToken: (delta, cid) => {
        if (completedRef.current) return
        if (!nextConversationIdRef.current) {
          nextConversationIdRef.current = cid
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
      onDone: (cid, usage) => {
        if (completedRef.current) return
        completedRef.current = true
        setConversationId(cid)
        setStreaming(false)
        if (usage) {
          setMessages((prev) => {
            const next = prev.slice()
            const last = next[next.length - 1]
            if (last && last.role === 'assistant') {
              next[next.length - 1] = { ...last, usage }
            }
            return next
          })
        }
      },
      onCancelled: () => {
        // 取消时强制结束流式状态，不检查 completedRef
        completedRef.current = true
        setStreaming(false)
        // 取消时保留已生成内容，不删除
      },
      onError: (message) => {
        if (completedRef.current) return
        completedRef.current = true
        setStreaming(false)
        setError(message)
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

    ws.connect()
    wsRef.current = ws

    return () => {
      ws.disconnect()
    }
  }, [])

  useEffect(() => {
    writeStoredConversationId(conversationId)
  }, [conversationId])

  const sendMessage = useCallback(
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
      completedRef.current = false
      nextConversationIdRef.current = conversationId

      wsRef.current?.send(content, conversationId ?? undefined, getUserId())
    },
    [streaming, conversationId],
  )

  const cancelStreaming = useCallback(function cancelStreaming() {
    if (!streaming) return
    wsRef.current?.cancel()
    // onDone/onCancelled 回调会处理状态更新
  }, [streaming])

  async function loadConversation(id: string) {
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
        cancelStreaming,
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
