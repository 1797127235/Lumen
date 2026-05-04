import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import * as Dialog from '@radix-ui/react-dialog'
import ReactMarkdown from 'react-markdown'
import {
  deleteConversation,
  getChatHistory,
  type ConversationSummary,
} from '../lib/api'
import { useChatSession } from '../lib/chatSession'

const EXAMPLES = [
  '我大三，后端方向，现在准备暑期实习还来得及吗？',
  '看看我简历里最弱的一项是什么？',
  '想去字节做后端，我还差多远？',
]

export default function Chat() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [draft, setDraft] = useState('')
  const {
    messages,
    streaming,
    conversationId,
    error,
    sendMessage,
    loadConversation,
    startNew,
  } = useChatSession()
  const endRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const fromUrl = searchParams.get('c')
    if (fromUrl) {
      if (fromUrl !== conversationId || messages.length === 0) {
        void loadConversation(fromUrl)
      }
      return
    }

    if (conversationId) {
      setSearchParams({ c: conversationId }, { replace: true })
      if (messages.length === 0 && !streaming) {
        void loadConversation(conversationId)
      }
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (conversationId) {
      setSearchParams({ c: conversationId }, { replace: true })
    } else {
      setSearchParams({}, { replace: true })
    }
  }, [conversationId, setSearchParams])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streaming])

  async function send() {
    const text = draft.trim()
    if (!text || streaming) return
    setDraft('')
    await sendMessage(text)
  }

  return (
    <div className="mx-auto flex min-h-[calc(100vh-64px)] max-w-[680px] flex-col px-md pt-xl pb-xl">
      <div className="sticky top-[64px] z-40 -mx-md mb-lg flex justify-end gap-md border-b border-border-soft bg-bg/85 px-md py-sm text-xs text-text-subtle backdrop-blur-md">
        <button
          onClick={startNew}
          className="hover:text-text"
          aria-label="开启新对话"
        >
          + 重新开始
        </button>
        <HistoryDrawer
          onPick={loadConversation}
          currentId={conversationId}
          onDeletedCurrent={startNew}
        />
      </div>

      <div className="flex flex-1 flex-col gap-xl">
        {messages.length === 0 && !streaming ? (
          <div className="mt-2xl flex flex-col gap-md ink-fade-in">
            <p className="text-lg text-text">
              我是码路，帮你看简历、对岗位、理方向。
            </p>
            <p className="text-lg text-text">我在等你说第一句。</p>
            <ul className="mt-md flex flex-col gap-2xs">
              {EXAMPLES.map((q) => (
                <li key={q} className="flex items-baseline gap-sm">
                  <span className="text-text-subtle">路</span>
                  <button
                    onClick={() => setDraft(q)}
                    className="text-left text-base text-text-muted transition-colors hover:text-ink"
                  >
                    {q}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {messages.map((message, index) =>
          message.role === 'assistant' ? (
            <AssistantBubble
              key={index}
              text={message.content}
              streaming={streaming && index === messages.length - 1}
            />
          ) : (
            <UserBubble key={index} text={message.content} />
          ),
        )}

        {error ? <p className="text-sm text-danger">{error}</p> : null}

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
      <div className="mb-2xs text-xs text-text-subtle">学长</div>
      <div className="mb-sm h-px w-12 bg-border" />
      <div className="prose prose-sm max-w-none text-base">
        <ReactMarkdown>{text}</ReactMarkdown>
        {streaming && text ? <span className="ink-cursor" /> : null}
      </div>
      {streaming && !text ? (
        <span className="text-text-muted ink-cursor">正在写...</span>
      ) : null}
    </div>
  )
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="ink-fade-in flex flex-col items-end">
      <div className="mb-2xs text-xs text-text-muted">你</div>
      <div className="max-w-[80%] whitespace-pre-wrap rounded-xl bg-ink-soft/15 px-md py-sm text-base text-text">
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
  onPick: (id: string) => Promise<void> | void
  currentId: string | null
  onDeletedCurrent?: () => void
}) {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<ConversationSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [deleteId, setDeleteId] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    getChatHistory(20)
      .then(setItems)
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [open])

  async function confirmDelete(idToDelete?: string) {
    const id = idToDelete || deleteId
    if (!id) return

    try {
      await deleteConversation(id)
      setItems((prev) => prev.filter((item) => item.conversation_id !== id))
      if (id === deleteId) setDeleteId(null)
      if (id === currentId) {
        onDeletedCurrent?.()
        setOpen(false)
      }
    } catch {
      alert('删除失败，请重试')
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <button className="hover:text-text" aria-label="历史会话">
          翻翻之前
        </button>
      </Dialog.Trigger>

      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-bg/60 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed top-0 right-0 z-[60] h-screen w-full overflow-y-auto border-l border-border-soft bg-surface p-lg sm:w-[380px]">
          <div className="mb-lg flex items-center justify-between">
            <Dialog.Title className="text-lg font-semibold text-ink">
              之前聊过的
            </Dialog.Title>
            <Dialog.Close className="rounded-md p-1.5 text-text-subtle transition-colors hover:bg-surface-elevated hover:text-text">
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M6 18L18 6M6 6l12 12"
                />
              </svg>
            </Dialog.Close>
          </div>
          <Dialog.Description className="sr-only">
            选择一段对话重新打开
          </Dialog.Description>

          {loading ? (
            <div className="mt-md ink-progress" />
          ) : items.length === 0 ? (
            <p className="text-sm text-text-muted">还没有聊过。</p>
          ) : (
            <ul className="flex flex-col gap-xs">
              {items.map((item) => (
                <li key={item.conversation_id}>
                  <button
                    onClick={() => {
                      void onPick(item.conversation_id)
                      setOpen(false)
                    }}
                    className={`group flex w-full items-start justify-between rounded-md px-3 py-2.5 text-left transition-colors hover:bg-surface-elevated ${
                      currentId === item.conversation_id ? 'bg-surface-elevated/50' : ''
                    }`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <svg className="h-4 w-4 flex-shrink-0 text-text-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={1.5}
                            d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
                          />
                        </svg>
                        <span className="truncate text-sm font-medium text-text transition-colors group-hover:text-ink">
                          {item.title || '未命名'}
                        </span>
                      </div>
                      <div className="mt-1 ml-6 flex items-center gap-1.5 text-xs text-text-subtle">
                        <time>{formatTime(item.last_message_at)}</time>
                        <span>·</span>
                        <span>{item.message_count} 条消息</span>
                      </div>
                    </div>
                    <div
                      onClick={(event) => {
                        event.stopPropagation()
                        if (deleteId === item.conversation_id) {
                          void confirmDelete(item.conversation_id)
                        } else {
                          setDeleteId(item.conversation_id)
                          setTimeout(() => setDeleteId(null), 3000)
                        }
                      }}
                      className={`mt-0.5 flex-shrink-0 text-xs transition-all ${
                        deleteId === item.conversation_id
                          ? 'text-danger opacity-100'
                          : 'text-text-subtle opacity-0 group-hover:opacity-100 hover:text-danger'
                      }`}
                    >
                      {deleteId === item.conversation_id ? '确定？' : '删掉'}
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
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''

  const now = new Date()
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()

  if (sameDay) {
    return `${pad(date.getHours())}:${pad(date.getMinutes())}`
  }

  return `${date.getMonth() + 1}月${date.getDate()}日`
}

function pad(n: number): string {
  return n < 10 ? `0${n}` : String(n)
}

const PLACEHOLDERS = [
  '说说你最近的情况...',
  '贴一段 JD，我帮你诊断匹配度',
  '想去大厂后端，我还差什么？',
  '简历怎么改才能更容易过初筛？',
]

function InputBox({
  draft,
  onChange,
  onSend,
  streaming,
}: {
  draft: string
  onChange: (value: string) => void
  onSend: () => void
  streaming: boolean
}) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const [phIndex, setPhIndex] = useState(0)
  const [focused, setFocused] = useState(false)

  useEffect(() => {
    if (draft) return
    const id = setInterval(() => {
      setPhIndex((index) => (index + 1) % PLACEHOLDERS.length)
    }, 4000)
    return () => clearInterval(id)
  }, [draft])

  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [draft])

  const canSend = draft.trim().length > 0 && !streaming

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      if (canSend) onSend()
    }
  }

  return (
    <div
      className={`
        relative rounded-2xl border transition-colors duration-200
        ${focused ? 'border-ink/40 bg-surface shadow-sm' : 'border-border bg-surface/50'}
      `}
    >
      <textarea
        ref={ref}
        value={draft}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        placeholder={PLACEHOLDERS[phIndex]}
        rows={1}
        className="w-full resize-none bg-transparent px-4 py-3 text-base leading-relaxed placeholder:text-text-subtle/60 focus:outline-none"
        style={{ maxHeight: '200px' }}
      />

      <div className="flex items-center justify-between px-3 pt-0 pb-2">
        <span className="select-none text-[11px] text-text-subtle/60">
          {draft.length > 0 ? `${draft.length} 字` : 'Shift + Enter 换行'}
        </span>

        <button
          onClick={onSend}
          disabled={!canSend}
          className={`
            flex h-8 w-8 items-center justify-center rounded-full transition-all duration-150
            ${
              canSend
                ? 'bg-ink text-bg hover:scale-105 hover:bg-ink-deep'
                : 'cursor-not-allowed bg-border text-text-subtle'
            }
          `}
          aria-label="发送消息"
        >
          {streaming ? (
            <span className="animate-pulse">...</span>
          ) : (
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M22 2L11 13" />
              <path d="M22 2l-7 20-4-9-9-4 20-7z" />
            </svg>
          )}
        </button>
      </div>
    </div>
  )
}
