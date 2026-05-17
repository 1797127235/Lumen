import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import ObservationStrip from '../components/ObservationStrip'
import { useChatSession } from '../lib/chatSession'
import { parseThinkSegments } from '../lib/thinkSegments'

export default function Chat() {
  const [draft, setDraft] = useState('')
  const {
    messages,
    streaming,
    error,
    sendMessage,
    cancelStreaming,
  } = useChatSession()
  const endRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streaming])

  async function send() {
    const text = draft.trim()
    if (!text || streaming) return
    setDraft('')
    try {
      await sendMessage(text)
    } catch {
      setDraft(text)
    }
  }

  return (
    <div className="mx-auto flex h-full max-w-[680px] flex-col px-md pt-xl pb-xl">
      <ObservationStrip />
      <div className="scroll-auto-hide flex min-h-0 flex-1 flex-col gap-xl overflow-y-auto">
        {messages.length === 0 && !streaming ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-md ink-fade-in">
            <p className="text-lg text-text">我是 Lumen，很高兴认识你。</p>
            <p className="text-lg text-text">从哪里开始都行。</p>
          </div>
        ) : null}

        {messages.map((message, index) =>
          message.role === 'assistant' ? (
              <AssistantBubble
              key={message.id}
              text={message.content}
              streaming={streaming && index === messages.length - 1}
              usage={message.usage}
              traces={message.traces}
              tokens_used={message.tokens_used}
            />
          ) : (
            <UserBubble key={message.id} text={message.content} />
          ),
        )}

        {streaming && messages[messages.length - 1]?.role !== 'assistant' ? (
          <AssistantBubble text="" streaming={true} />
        ) : null}

        {error ? <p className="text-sm text-danger">{error}</p> : null}

        <div ref={endRef} />
      </div>

      <div className="mt-xl">
        <InputBox
          draft={draft}
          onChange={setDraft}
          onSend={send}
          streaming={streaming}
          onCancel={cancelStreaming}
        />
      </div>
    </div>
  )
}

function AssistantBubble({
  text,
  streaming,
  usage,
  traces,
  tokens_used,
}: {
  text: string
  streaming: boolean
  usage?: { input: number; output: number }
  traces?: import('../lib/chatSession').TraceEntry[]
  tokens_used?: number
}) {
  const segments = parseThinkSegments(text)

  return (
    <div className="ink-fade-in">
      <div className="mb-2xs text-xs text-text-subtle">Lumen</div>
      <div className="mb-sm h-px w-12 bg-border" />

      {traces && traces.length > 0 ? (
        <TracePanel traces={traces} streaming={streaming} />
      ) : null}

      {segments.map((seg, i) =>
        seg.kind === 'think' ? (
          <ThinkingCard key={i} content={seg.content} closed={seg.closed} />
        ) : (
          <div key={i} className="prose prose-sm max-w-none text-base">
            <ReactMarkdown>{seg.content}</ReactMarkdown>
            {streaming && i === segments.length - 1 && seg.content ? (
              <span className="ink-cursor" />
            ) : null}
          </div>
        ),
      )}

      {streaming && !text ? (
        <span className="text-text-muted ink-cursor">正在写...</span>
      ) : null}

      {usage && !streaming ? (
        <div className="mt-xs flex gap-xs text-[11px] text-text-subtle/50">
          <span>输入 {usage.input}</span>
          <span>·</span>
          <span>输出 {usage.output}</span>
          <span>token</span>
        </div>
      ) : tokens_used && !streaming ? (
        <div className="mt-xs text-[11px] text-text-subtle/50">
          <span>{tokens_used} token</span>
        </div>
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

const PLACEHOLDERS = [
  '说说你最近的情况...',
  '有什么在脑子里转？',
  '最近在纠结什么？',
  '想理清楚什么？',
]

function InputBox({
  draft,
  onChange,
  onSend,
  streaming,
  onCancel,
}: {
  draft: string
  onChange: (value: string) => void
  onSend: () => void
  streaming: boolean
  onCancel: () => void
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
        <div className="flex items-center gap-xs">
          <span className="select-none text-[11px] text-text-subtle/60">
            {draft.length > 0 ? `${draft.length} 字` : 'Shift + Enter 换行'}
          </span>
        </div>

        <button
          onClick={streaming ? onCancel : onSend}
          disabled={!streaming && !canSend}
          className={`
            flex h-8 w-8 items-center justify-center rounded-full transition-all duration-150
            ${
              streaming
                ? 'bg-danger text-bg hover:scale-105 hover:bg-danger/80'
                : canSend
                  ? 'bg-ink text-bg hover:scale-105 hover:bg-ink-deep'
                  : 'cursor-not-allowed bg-border text-text-subtle'
            }
          `}
          aria-label={streaming ? '停止生成' : '发送消息'}
        >
          {streaming ? (
            <svg
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="currentColor"
            >
              <rect x="4" y="4" width="16" height="16" rx="2" />
            </svg>
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

function ThinkingCard({ content, closed }: { content: string; closed: boolean }) {
  const [userToggled, setUserToggled] = useState<boolean | null>(null)
  const detailsRef = useRef<HTMLDetailsElement>(null)

  const open = userToggled !== null ? userToggled : !closed

  useEffect(() => {
    const el = detailsRef.current
    if (el && el.open !== open) el.open = open
  }, [open])

  return (
    <details
      ref={detailsRef}
      onToggle={(e) => {
        const next = e.currentTarget.open
        if (next !== open) setUserToggled(next)
      }}
      className="group/think my-sm overflow-hidden rounded-lg border border-border-soft bg-surface/50"
    >
      <summary className="flex cursor-pointer list-none items-center gap-xs px-sm py-xs text-xs text-text-subtle transition-colors hover:text-text-muted [&::-webkit-details-marker]:hidden">
        <svg
          className="h-3 w-3 shrink-0 transition-transform group-open/think:rotate-180"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
        <span>{closed ? '思考完成' : '思考中...'}</span>
        {!closed && (
          <span className="ml-xs animate-pulse text-text-subtle">·</span>
        )}
        {closed && content && (
          <span className="ml-auto text-text-subtle/40">{content.length} 字</span>
        )}
      </summary>
      <div className="border-t border-border-soft px-sm py-xs">
        <pre className="whitespace-pre-wrap font-mono text-xs text-text-subtle leading-relaxed">
          {content || '思考中…'}
        </pre>
      </div>
    </details>
  )
}

const TOOL_LABELS: Record<string, string> = {
  memory_search: '搜索记忆',
  memory_save: '保存记忆',
  update_profile: '更新画像',
  get_profile: '读取画像',
  thinking: '思考',
}

function TracePanel({
  traces,
  streaming,
}: {
  traces: import('../lib/chatSession').TraceEntry[]
  streaming: boolean
}) {
  if (!traces.length) return null

  return (
    <div className="mb-sm flex flex-col gap-2xs">
      {traces.map((trace, i) => (
        <details
          key={i}
          open={!trace.done && streaming}
          className="group/trace overflow-hidden rounded-lg border border-border-soft bg-surface/40"
        >
          <summary className="flex cursor-pointer list-none items-center gap-xs px-sm py-[5px] text-xs text-text-subtle hover:text-text-muted [&::-webkit-details-marker]:hidden">
            <svg
              className="h-3 w-3 shrink-0 transition-transform group-open/trace:rotate-180"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
            <span>
              {trace.tool === 'thinking'
                ? trace.done && trace.duration
                  ? `思考 · 用时 ${trace.duration}`
                  : '思考中...'
                : TOOL_LABELS[trace.tool] ?? trace.tool}
            </span>
            {!trace.done && streaming && (
              <span className="ml-xs animate-pulse text-text-subtle">···</span>
            )}
          </summary>
          <div className="border-t border-border-soft px-sm py-xs space-y-2xs">
            {trace.tool === 'thinking' ? (
              <div className="text-xs text-text-subtle whitespace-pre-wrap">
                {trace.thinking || '...'}
              </div>
            ) : (
              <>
                {trace.args && (
                  <div className="text-xs text-text-subtle">
                    <span className="text-text-subtle/60">参数 </span>
                    <span className="font-mono">{trace.args}</span>
                  </div>
                )}
                {trace.result && (
                  <div className="text-xs text-text-subtle">
                    <span className="text-text-subtle/60">结果 </span>
                    <span>{trace.result}</span>
                  </div>
                )}
              </>
            )}
          </div>
        </details>
      ))}
    </div>
  )
}
