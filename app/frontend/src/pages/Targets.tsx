import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import * as Dialog from '@radix-ui/react-dialog'
import {
  DndContext,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core'
import {
  createTarget,
  getBoard,
  updateTarget,
  type BoardResponse,
  type TargetCard,
  type TargetStatus,
} from '../lib/api'

const STATUS_ORDER: TargetStatus[] = [
  'interested',
  'applied',
  'test',
  'interview',
  'offer',
  'rejected',
  'abandoned',
]

const STATUS_LABEL: Record<TargetStatus, string> = {
  interested: '感兴趣',
  applied: '已投递',
  test: '笔试',
  interview: '面试',
  offer: 'Offer',
  rejected: '被拒',
  abandoned: '放弃',
}

export default function TargetsPage() {
  const [board, setBoard] = useState<BoardResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [activeId, setActiveId] = useState<string | null>(null)

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  )

  useEffect(() => {
    void load()
  }, [])

  async function load() {
    try {
      const data = await getBoard()
      setBoard(data)
    } catch (e) {
      setError((e as Error).message || '加载失败')
    }
  }

  function handleDragStart(e: DragStartEvent) {
    setActiveId(String(e.active.id))
  }

  async function handleDragEnd(e: DragEndEvent) {
    setActiveId(null)
    const { active, over } = e
    if (!over || !board) return
    const targetId = String(active.id)
    const toStatus = String(over.id) as TargetStatus
    const fromStatus = findStatus(board, targetId)
    if (!fromStatus || fromStatus === toStatus) return

    const card = board.columns[fromStatus]?.find((c) => c.target_id === targetId)
    if (!card) return

    const next: BoardResponse = {
      ...board,
      columns: {
        ...board.columns,
        [fromStatus]: board.columns[fromStatus].filter(
          (c) => c.target_id !== targetId,
        ),
        [toStatus]: [...(board.columns[toStatus] ?? []), { ...card, status: toStatus }],
      },
    }
    setBoard(next)

    try {
      await updateTarget(targetId, { status: toStatus })
    } catch (err) {
      setError((err as Error).message || '更新状态失败')
      void load()
    }
  }

  return (
    <div className="px-md md:px-lg py-xl flex flex-col gap-lg">
      <header className="flex items-center justify-between">
        <h2 className="text-2xl">岗位追踪</h2>
        <button
          onClick={() => setCreating(true)}
          className="text-ink hover:text-ink-deep text-base"
        >
          + 新增岗位
        </button>
      </header>

      {board ? <StatsBar stats={board.stats} /> : null}

      {error ? <p className="text-danger text-sm">{error}</p> : null}

      <DndContext
        sensors={sensors}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
      >
        <div className="flex gap-md overflow-x-auto pb-md">
          {STATUS_ORDER.map((status) => (
            <Column
              key={status}
              status={status}
              cards={board?.columns[status] ?? []}
              activeId={activeId}
            />
          ))}
        </div>
      </DndContext>

      <CreateDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={() => {
          setCreating(false)
          void load()
        }}
      />
    </div>
  )
}

function findStatus(board: BoardResponse, targetId: string): TargetStatus | null {
  for (const status of STATUS_ORDER) {
    if (board.columns[status]?.some((c) => c.target_id === targetId)) {
      return status
    }
  }
  return null
}

function StatsBar({ stats }: { stats: BoardResponse['stats'] }) {
  return (
    <div className="flex flex-wrap items-center gap-md text-sm text-text-muted">
      <span>共 {stats.total} 个岗位</span>
      <span className="text-text-subtle">·</span>
      <span>平均匹配 {stats.avg_score}%</span>
      {stats.common_gaps.length > 0 ? (
        <>
          <span className="text-text-subtle">·</span>
          <span>高频缺口: {stats.common_gaps.join('、')}</span>
        </>
      ) : null}
    </div>
  )
}

function Column({
  status,
  cards,
  activeId,
}: {
  status: TargetStatus
  cards: TargetCard[]
  activeId: string | null
}) {
  const { setNodeRef, isOver } = useDroppable({ id: status })
  return (
    <div
      ref={setNodeRef}
      className={[
        'flex-shrink-0 w-[260px] flex flex-col gap-sm rounded-md border p-sm',
        isOver ? 'border-ink bg-surface-elevated' : 'border-border-soft bg-surface',
      ].join(' ')}
    >
      <div className="flex items-center justify-between text-sm text-text-muted">
        <span>{STATUS_LABEL[status]}</span>
        <span className="text-text-subtle">{cards.length}</span>
      </div>
      <div className="flex flex-col gap-sm min-h-[40px]">
        {cards.map((card) => (
          <DraggableCard
            key={card.target_id}
            card={card}
            dimmed={activeId === card.target_id}
          />
        ))}
      </div>
    </div>
  )
}

function DraggableCard({ card, dimmed }: { card: TargetCard; dimmed: boolean }) {
  const navigate = useNavigate()
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: card.target_id,
  })
  const style = transform
    ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` }
    : undefined

  function handleClick(e: React.MouseEvent) {
    if (isDragging) return
    e.stopPropagation()
    navigate(`/targets/${card.target_id}`)
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      onClick={handleClick}
      className={[
        'rounded-md border border-border-soft bg-bg p-sm cursor-grab active:cursor-grabbing',
        'hover:border-border transition-colors',
        dimmed ? 'opacity-40' : '',
      ].join(' ')}
    >
      <div className="flex items-baseline justify-between gap-xs">
        <span className="text-base text-text truncate">{card.company}</span>
        {card.match_score !== null && card.match_score !== undefined ? (
          <span className="text-xs text-ink shrink-0">{card.match_score}%</span>
        ) : null}
      </div>
      <div className="text-sm text-text-muted truncate">
        {card.title}
        {card.status === 'interview' && card.interview_round
          ? ` · ${card.interview_round}`
          : ''}
      </div>
      {card.agent_advice ? (
        <p className="mt-xs text-xs text-text-subtle line-clamp-2">
          {card.agent_advice}
        </p>
      ) : (
        <p className="mt-xs text-xs text-text-subtle italic">建议生成中…</p>
      )}
      <div className="mt-xs flex items-center justify-between text-xs text-text-subtle">
        <span>{card.location ?? ''}</span>
        <span>{formatDate(card.created_at)}</span>
      </div>
    </div>
  )
}

function formatDate(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  return d.getFullYear() === now.getFullYear()
    ? `${d.getMonth() + 1}-${d.getDate()}`
    : `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`
}

function CreateDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  onCreated: () => void
}) {
  const [company, setCompany] = useState('')
  const [title, setTitle] = useState('')
  const [location, setLocation] = useState('')
  const [salary, setSalary] = useState('')
  const [jdText, setJdText] = useState('')
  const [jdUrl, setJdUrl] = useState('')
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const canSubmit = useMemo(
    () => company.trim().length > 0 && title.trim().length > 0 && !busy,
    [company, title, busy],
  )

  function reset() {
    setCompany('')
    setTitle('')
    setLocation('')
    setSalary('')
    setJdText('')
    setJdUrl('')
    setNotes('')
    setErr(null)
  }

  async function submit() {
    if (!canSubmit) return
    setBusy(true)
    setErr(null)
    try {
      await createTarget({
        company: company.trim(),
        title: title.trim(),
        location: location.trim() || null,
        salary: salary.trim() || null,
        jd_text: jdText.trim() || null,
        jd_url: jdUrl.trim() || null,
        notes: notes.trim() || null,
      })
      onCreated()
    } catch (e) {
      setErr((e as Error).message || '创建失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(v) => {
        if (!v) reset()
        onOpenChange(v)
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-bg/60 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[92vw] sm:w-[480px] max-h-[90vh] overflow-y-auto bg-surface border border-border-soft rounded-md p-lg">
          <Dialog.Title className="text-lg mb-md">新增岗位</Dialog.Title>
          <Dialog.Description className="sr-only">
            填写岗位信息，可选填 JD 文本以触发自动诊断
          </Dialog.Description>

          <div className="flex flex-col gap-sm">
            <Field label="公司" required>
              <input
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                className="w-full bg-bg border border-border-soft rounded-sm px-sm py-2xs"
              />
            </Field>
            <Field label="岗位" required>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="w-full bg-bg border border-border-soft rounded-sm px-sm py-2xs"
              />
            </Field>
            <div className="grid grid-cols-2 gap-sm">
              <Field label="城市">
                <input
                  value={location}
                  onChange={(e) => setLocation(e.target.value)}
                  className="w-full bg-bg border border-border-soft rounded-sm px-sm py-2xs"
                />
              </Field>
              <Field label="薪资">
                <input
                  value={salary}
                  onChange={(e) => setSalary(e.target.value)}
                  className="w-full bg-bg border border-border-soft rounded-sm px-sm py-2xs"
                />
              </Field>
            </div>
            <Field label="JD 链接">
              <input
                value={jdUrl}
                onChange={(e) => setJdUrl(e.target.value)}
                placeholder="可选"
                className="w-full bg-bg border border-border-soft rounded-sm px-sm py-2xs"
              />
            </Field>
            <Field label="JD 文本">
              <textarea
                value={jdText}
                onChange={(e) => setJdText(e.target.value)}
                rows={5}
                placeholder="贴上 JD 我顺手帮你诊断"
                className="w-full bg-bg border border-border-soft rounded-sm px-sm py-2xs resize-y"
              />
            </Field>
            <Field label="备注">
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={2}
                className="w-full bg-bg border border-border-soft rounded-sm px-sm py-2xs resize-y"
              />
            </Field>
          </div>

          {err ? <p className="text-danger text-sm mt-sm">{err}</p> : null}

          <div className="mt-md flex items-center justify-end gap-md">
            <Dialog.Close asChild>
              <button className="text-text-muted hover:text-text text-sm">取消</button>
            </Dialog.Close>
            <button
              onClick={submit}
              disabled={!canSubmit}
              className="text-ink hover:text-ink-deep text-base disabled:text-text-subtle disabled:cursor-not-allowed"
            >
              {busy ? '…' : '保存'}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function Field({
  label,
  required,
  children,
}: {
  label: string
  required?: boolean
  children: React.ReactNode
}) {
  return (
    <label className="flex flex-col gap-2xs text-sm text-text-muted">
      <span>
        {label}
        {required ? <span className="text-danger">*</span> : null}
      </span>
      {children}
    </label>
  )
}
