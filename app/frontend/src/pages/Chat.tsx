import { useEffect, useRef, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import {
  chatStream,
  getChatHistory,
  getConversation,
  type ConversationSummary,
} from '../lib/api'

type Msg = { role: 'user' | 'assistant'; content: string }

const EXAMPLES = [
  '我大三, 后端方向, 还来得及吗?',
  '看看我简历里最弱的一项是什么.',
  '想去字节做后端, 我差多远?',
]

export default function Chat() {
  const [messages, setMessages] = useState<Msg[]>([])
  const [draft, setDraft] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const endRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streaming])

  async function send() {
    const text = draft.trim()
    if (!text || streaming) return

    setMessages((m) => [
      ...m,
      { role: 'user', content: text },
      { role: 'assistant', content: '' },
    ])
    setDraft('')
    setStreaming(true)
    setError(null)

    const ctrl = new AbortController()
    abortRef.current = ctrl

    let convId = conversationId
    await chatStream(text, conversationId, {
      signal: ctrl.signal,
      onToken: (delta, cid) => {
        if (!convId) {
          convId = cid
          setConversationId(cid)
        }
        if (!delta) return
        setMessages((m) => {
          const next = m.slice()
          const last = next[next.length - 1]
          if (last && last.role === 'assistant') {
            next[next.length - 1] = { ...last, content: last.content + delta }
          }
          return next
        })
      },
      onDone: () => {
        setStreaming(false)
      },
      onError: (msg) => {
        setStreaming(false)
        setError(msg)
      },
    })
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      send()
    }
  }

  async function loadConversation(id: string) {
    try {
      const items = await getConversation(id)
      setMessages(
        items
          .filter((m) => m.role === 'user' || m.role === 'assistant')
          .map((m) => ({
            role: m.role as 'user' | 'assistant',
            content: m.content ?? '',
          })),
      )
      setConversationId(id)
    } catch (e) {
      setError((e as Error).message || '加载会话失败')
    }
  }

  function startNew() {
    abortRef.current?.abort()
    setMessages([])
    setConversationId(null)
    setStreaming(false)
    setError(null)
  }

  return (
    <div className="mx-auto max-w-[680px] px-md py-xl flex flex-col min-h-[calc(100vh-64px)]">
      <div className="flex justify-end gap-md text-xs text-text-subtle mb-lg">
        <button
          onClick={startNew}
          className="hover:text-text"
          aria-label="开新一轮对话"
        >
          + 重开一段
        </button>
        <HistoryDrawer onPick={loadConversation} />
      </div>

      <div className="flex-1 flex flex-col gap-xl">
        {messages.length === 0 && !streaming ? (
          <div className="mt-2xl flex flex-col gap-md ink-fade-in">
            <p className="text-text text-lg">
              我是码路 — 帮你看简历、对岗位、理方向.
            </p>
            <p className="text-text text-lg">我在等你说第一句.</p>
            <ul className="flex flex-col gap-2xs mt-md">
              {EXAMPLES.map((q) => (
                <li key={q} className="flex items-baseline gap-sm">
                  <span className="text-text-subtle">·</span>
                  <button
                    onClick={() => setDraft(q)}
                    className="text-base text-text-muted hover:text-ink text-left transition-colors"
                  >
                    {q}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {messages.map((m, i) =>
          m.role === 'assistant' ? (
            <AssistantBubble
              key={i}
              text={m.content}
              streaming={streaming && i === messages.length - 1}
            />
          ) : (
            <UserBubble key={i} text={m.content} />
          ),
        )}

        {error ? (
          <p className="text-danger text-sm">{error}</p>
        ) : null}

        <div ref={endRef} />
      </div>

      <div className="mt-xl pt-md border-t border-border">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="跟我说说..."
          rows={2}
          className="w-full resize-none text-base leading-relaxed placeholder:text-text-subtle"
        />
        <div className="flex justify-between items-center text-xs text-text-subtle">
          <span>Enter 发送 · Shift+Enter 换行</span>
          <button
            onClick={send}
            disabled={streaming || !draft.trim()}
            className="text-ink hover:text-ink-deep disabled:text-text-subtle disabled:cursor-not-allowed"
            aria-label="发送"
          >
            {streaming ? '…' : '↩'}
          </button>
        </div>
      </div>
    </div>
  )
}

function AssistantBubble({ text, streaming }: { text: string; streaming: boolean }) {
  return (
    <div className="ink-fade-in">
      <div className="text-xs text-text-subtle mb-2xs">学长</div>
      <div className="h-px w-12 bg-border mb-sm" />
      <div className="text-base whitespace-pre-wrap">
        {text}
        {streaming && text ? <span className="ink-cursor" /> : null}
      </div>
      {streaming && !text ? (
        <span className="text-text-muted ink-cursor">正在写</span>
      ) : null}
    </div>
  )
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="ink-fade-in flex flex-col items-end">
      <div className="text-xs text-text-muted mb-2xs">你</div>
      <div className="rounded-xl bg-ink-soft/15 text-text px-md py-sm max-w-[80%] text-base whitespace-pre-wrap">
        {text}
      </div>
    </div>
  )
}

function HistoryDrawer({ onPick }: { onPick: (id: string) => void }) {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<ConversationSummary[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!open) return
    getChatHistory(20)
      .then(setItems)
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [open])

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <button className="hover:text-text" aria-label="历史会话">
          翻翻之前
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-bg/60 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed top-0 right-0 h-screen w-full sm:w-[380px] bg-surface border-l border-border-soft p-lg overflow-y-auto">
          <Dialog.Title className="text-lg mb-md">之前聊过的</Dialog.Title>
          <Dialog.Description className="sr-only">
            选一段重新打开
          </Dialog.Description>

          {loading ? (
            <div className="ink-progress mt-md" />
          ) : items.length === 0 ? (
            <p className="text-text-muted text-sm">还没有过对话.</p>
          ) : (
            <ul className="flex flex-col gap-sm">
              {items.map((c) => (
                <li key={c.conversation_id}>
                  <button
                    onClick={() => {
                      onPick(c.conversation_id)
                      setOpen(false)
                    }}
                    className="block w-full text-left py-sm border-b border-border-soft hover:text-ink"
                  >
                    <div className="text-base text-text truncate">
                      {c.title || '未命名'}
                    </div>
                    <div className="text-xs text-text-subtle">
                      {formatTime(c.last_message_at)} · {c.message_count} 条
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function formatTime(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  if (sameDay) {
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`
  }
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

function pad(n: number): string {
  return n < 10 ? `0${n}` : String(n)
}
