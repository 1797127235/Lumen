import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import * as Dialog from '@radix-ui/react-dialog'
import ReactMarkdown from 'react-markdown'
import {
  chatStream,
  deleteConversation,
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
  const [searchParams, setSearchParams] = useSearchParams()
  const [messages, setMessages] = useState<Msg[]>([])
  const [draft, setDraft] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(
    searchParams.get('c'),
  )
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const endRef = useRef<HTMLDivElement | null>(null)

  // 页面加载时从 URL 恢复对话
  useEffect(() => {
    const cid = searchParams.get('c')
    if (cid) {
      loadConversation(cid)
    }
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  // conversationId 变化时同步到 URL
  useEffect(() => {
    if (conversationId) {
      setSearchParams({ c: conversationId }, { replace: true })
    } else {
      setSearchParams({}, { replace: true })
    }
  }, [conversationId])  // eslint-disable-line react-hooks/exhaustive-deps

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
    <div className="mx-auto max-w-[680px] px-md pt-xl pb-xl flex flex-col min-h-[calc(100vh-64px)]">
      {/* 长会话时随页面滚动仍贴在视口顶部（全局导航下方），不必滚回页面最顶端 */}
      <div className="sticky top-[64px] z-40 -mx-md px-md py-sm mb-lg flex justify-end gap-md text-xs text-text-subtle border-b border-border-soft bg-bg/85 backdrop-blur-md">
        <button
          onClick={startNew}
          className="hover:text-text"
          aria-label="开新一轮对话"
        >
          + 重开一段
        </button>
        <HistoryDrawer
          onPick={loadConversation}
          currentId={conversationId}
          onDeletedCurrent={startNew}
        />
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

      <div className="mt-xl">
        <InputBox
          draft={draft}
          onChange={setDraft}
          onSend={send}
          streaming={streaming}
        />
      </div>
    </div>
  )
}

function AssistantBubble({ text, streaming }: { text: string; streaming: boolean }) {
  return (
    <div className="ink-fade-in">
      <div className="text-xs text-text-subtle mb-2xs">学长</div>
      <div className="h-px w-12 bg-border mb-sm" />
      <div className="text-base prose prose-sm max-w-none">
        <ReactMarkdown>{text}</ReactMarkdown>
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

function HistoryDrawer({
  onPick,
  currentId,
  onDeletedCurrent,
}: {
  onPick: (id: string) => void
  currentId: string | null
  onDeletedCurrent?: () => void
}) {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<ConversationSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [deleteId, setDeleteId] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    getChatHistory(20)
      .then(setItems)
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [open])

  async function confirmDelete(idToDel?: string) {
    const id = idToDel || deleteId
    if (!id) return
    try {
      await deleteConversation(id)
      setItems((prev) => prev.filter((c) => c.conversation_id !== id))
      if (id === deleteId) setDeleteId(null)
      if (id === currentId) {
        onDeletedCurrent?.()
        setOpen(false)
      }
    } catch {
      alert('删除失败,请重试')
    }
  }

  return (
    <>
      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Trigger asChild>
          <button className="hover:text-text" aria-label="历史会话">
            翻翻之前
          </button>
        </Dialog.Trigger>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-bg/60 backdrop-blur-[2px]" />
          <Dialog.Content className="fixed top-0 right-0 z-[60] h-screen w-full sm:w-[380px] bg-surface border-l border-border-soft p-lg overflow-y-auto">
            <div className="flex items-center justify-between mb-lg">
              <Dialog.Title className="text-lg font-semibold text-ink">之前聊过的</Dialog.Title>
              <Dialog.Close className="p-1.5 rounded-md text-text-subtle hover:text-text hover:bg-surface-elevated transition-colors">
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </Dialog.Close>
            </div>
            <Dialog.Description className="sr-only">
              选一段重新打开
            </Dialog.Description>

            {loading ? (
              <div className="ink-progress mt-md" />
            ) : items.length === 0 ? (
              <p className="text-text-muted text-sm">还没有过对话.</p>
            ) : (
              <ul className="flex flex-col gap-xs">
                {items.map((c) => (
                  <li key={c.conversation_id}>
                    <button
                      onClick={() => {
                        onPick(c.conversation_id)
                        setOpen(false)
                      }}
                      className={`group flex items-start justify-between w-full text-left rounded-md px-3 py-2.5 transition-colors hover:bg-surface-elevated ${currentId === c.conversation_id ? 'bg-surface-elevated/50' : ''}`}
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <svg className="w-4 h-4 text-text-subtle flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                          </svg>
                          <span className="text-sm font-medium text-text truncate group-hover:text-ink transition-colors">
                            {c.title || '未命名'}
                          </span>
                        </div>
                        <div className="mt-1 ml-6 flex items-center gap-1.5 text-xs text-text-subtle">
                          <time>{formatTime(c.last_message_at)}</time>
                          <span>·</span>
                          <span>{c.message_count} 条消息</span>
                        </div>
                      </div>
                      <div
                        onClick={(e) => {
                          e.stopPropagation()
                          if (deleteId === c.conversation_id) {
                            confirmDelete(c.conversation_id)
                          } else {
                            setDeleteId(c.conversation_id)
                            // 3秒后自动恢复
                            setTimeout(() => setDeleteId(null), 3000)
                          }
                        }}
                        className={`text-xs transition-all flex-shrink-0 mt-0.5 ${
                          deleteId === c.conversation_id
                            ? 'text-danger opacity-100'
                            : 'text-text-subtle opacity-0 group-hover:opacity-100 hover:text-danger'
                        }`}
                      >
                        {deleteId === c.conversation_id ? '确定?' : '删掉'}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
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

// ── 输入框 ──

const PLACEHOLDERS = [
  '说说你的近况…',
  '粘贴一段 JD，我帮你诊断匹配度',
  '想去大厂后端，还差什么？',
  '简历怎么改才能过初筛？',
]

function InputBox({
  draft,
  onChange,
  onSend,
  streaming,
}: {
  draft: string
  onChange: (v: string) => void
  onSend: () => void
  streaming: boolean
}) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const [phIndex, setPhIndex] = useState(0)
  const [focused, setFocused] = useState(false)

  // 轮播 placeholder
  useEffect(() => {
    if (draft) return
    const id = setInterval(() => {
      setPhIndex((i) => (i + 1) % PLACEHOLDERS.length)
    }, 4000)
    return () => clearInterval(id)
  }, [draft])

  // auto-grow
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [draft])

  const canSend = draft.trim().length > 0 && !streaming

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      if (canSend) onSend()
    }
  }

  return (
    <div
      className={`
        relative rounded-2xl border transition-colors duration-200
        ${focused
          ? 'border-ink/40 bg-surface shadow-sm'
          : 'border-border bg-surface/50'
        }
      `}
    >
      <textarea
        ref={ref}
        value={draft}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        placeholder={PLACEHOLDERS[phIndex]}
        rows={1}
        className="w-full resize-none bg-transparent px-4 py-3 text-base leading-relaxed placeholder:text-text-subtle/60 focus:outline-none"
        style={{ maxHeight: '200px' }}
      />

      <div className="flex items-center justify-between px-3 pb-2 pt-0">
        <span className="text-[11px] text-text-subtle/60 select-none">
          {draft.length > 0 ? `${draft.length} 字` : 'Shift + Enter 换行'}
        </span>

        <button
          onClick={onSend}
          disabled={!canSend}
          className={`
            flex items-center justify-center rounded-full transition-all duration-150
            ${canSend
              ? 'bg-ink text-bg hover:bg-ink-deep hover:scale-105'
              : 'bg-border text-text-subtle cursor-not-allowed'
            }
            w-8 h-8
          `}
          aria-label="发送"
        >
          {streaming ? (
            <span className="animate-pulse">…</span>
          ) : (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 2L11 13" />
              <path d="M22 2l-7 20-4-9-9-4 20-7z" />
            </svg>
          )}
        </button>
      </div>
    </div>
  )
}
