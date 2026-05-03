import { useEffect, useMemo, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import * as Dialog from '@radix-ui/react-dialog'
import {
  createTarget,
  getJDDiagnosis,
  type JDDiagnoseResponse,
  type GapSkill,
} from '../lib/api'

const PRIORITY_HINT: Record<string, string> = {
  high: '先补这个',
  medium: '可以补',
  low: '再说',
}

export default function JDReportPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [data, setData] = useState<JDDiagnoseResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showAddDialog, setShowAddDialog] = useState(false)
  const [addedToBoard, setAddedToBoard] = useState(false)

  useEffect(() => {
    if (!id) return
    let cancelled = false
    getJDDiagnosis(id)
      .then((res) => {
        if (!cancelled) setData(res)
      })
      .catch((e) => {
        if (!cancelled) setError((e as Error).message || '加载失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [id])

  function handleRediagnose() {
    if (data?.jd_text) {
      // 带着 jd_text 回到 JD 页（通过 state 传递）
      navigate('/jd', { state: { jdText: data.jd_text } })
    }
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-[820px] px-md py-xl">
        <div className="ink-progress mt-2xl" />
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="mx-auto max-w-[820px] px-md py-xl flex flex-col gap-md">
        <p className="text-danger">{error || '诊断记录不存在'}</p>
        <Link to="/jd" className="text-ink hover:text-ink-deep">
          ← 返回 JD 诊断
        </Link>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[820px] px-md py-xl flex flex-col gap-xl">
      {/* 顶部导航 */}
      <div className="flex items-center justify-between">
        <Link
          to="/jd"
          className="text-text-muted hover:text-ink text-sm"
        >
          ← 返回 JD 诊断
        </Link>
        <div className="flex items-center gap-md">
          {data.jd_text ? (
            <button
              onClick={handleRediagnose}
              className="text-text-subtle hover:text-ink text-sm"
            >
              重新诊断
            </button>
          ) : null}
          {addedToBoard ? (
            <>
              <span className="text-sm text-text-subtle">已加入看板</span>
              <Link
                to="/targets"
                className="text-ink hover:text-ink-deep text-sm"
              >
                去看板查看
              </Link>
            </>
          ) : (
            <button
              onClick={() => setShowAddDialog(true)}
              className="text-ink hover:text-ink-deep text-sm"
            >
              加入看板
            </button>
          )}
        </div>
      </div>

      {/* 报告内容 */}
      <ReportContent data={data} />

      <AddToBoardDialog
        open={showAddDialog}
        onOpenChange={setShowAddDialog}
        diagnosisId={id ?? ''}
        defaultTitle={data.jd_title}
        onSuccess={() => {
          setShowAddDialog(false)
          setAddedToBoard(true)
        }}
      />
    </div>
  )
}

function ReportContent({ data }: { data: JDDiagnoseResponse }) {
  return (
    <div className="flex flex-col gap-lg ink-fade-in">
      {/* 标题 + 总结 */}
      <div className="flex flex-col gap-xs">
        {data.jd_title ? (
          <h2 className="text-2xl">{data.jd_title}</h2>
        ) : null}
        {data.summary ? (
          <p className="text-text-muted text-base">{data.summary}</p>
        ) : null}
      </div>

      <div className="h-px w-full bg-border-soft" />

      {/* 已匹配技能 */}
      {data.matched_skills.length > 0 ? (
        <Section caption="你已经具备的技能">
          <BulletList items={data.matched_skills} />
        </Section>
      ) : null}

      {/* 优势 */}
      {data.strengths.length > 0 ? (
        <Section caption="你的优势">
          <BulletList items={data.strengths} />
        </Section>
      ) : null}

      {/* 缺口 */}
      {data.skill_gaps.length > 0 ? (
        <Section caption="你还缺的">
          <ul className="flex flex-col gap-xs">
            {data.skill_gaps.map((g, i) => (
              <GapItem key={i} g={g} />
            ))}
          </ul>
        </Section>
      ) : null}

      {/* 风险 */}
      {data.risks.length > 0 ? (
        <Section caption="提个醒">
          <BulletList items={data.risks} />
        </Section>
      ) : null}

      {/* 行动计划 */}
      {data.action_plan.length > 0 ? (
        <Section caption="下一步建议">
          <ol className="flex flex-col gap-sm">
            {data.action_plan.map((a, i) => (
              <li key={i} className="flex gap-md text-base text-text">
                <span className="font-mono text-text-subtle min-w-[1.5rem]">
                  {i + 1}.
                </span>
                <span className="flex-1">{a}</span>
              </li>
            ))}
          </ol>
        </Section>
      ) : null}

      {/* 简历建议 */}
      {data.resume_tips.length > 0 ? (
        <Section caption="改简历的话">
          <BulletList items={data.resume_tips} />
        </Section>
      ) : null}
    </div>
  )
}

function Section({
  caption,
  children,
}: {
  caption: string
  children: React.ReactNode
}) {
  return (
    <section className="flex flex-col gap-sm">
      <h3 className="text-lg">{caption}</h3>
      {children}
    </section>
  )
}

function BulletList({ items }: { items: string[] }) {
  return (
    <ul className="flex flex-col gap-2xs">
      {items.map((s, i) => (
        <li key={i} className="flex items-baseline gap-sm text-base text-text">
          <span className="text-text-subtle">·</span>
          <span className="flex-1">{s}</span>
        </li>
      ))}
    </ul>
  )
}

function GapItem({ g }: { g: GapSkill }) {
  const hint = PRIORITY_HINT[g.priority] ?? g.priority
  const color = g.priority === 'high' ? 'text-ink' : 'text-text-muted'
  return (
    <li className="flex items-baseline gap-sm text-base text-text">
      <span className="text-text-subtle">·</span>
      <span className="flex-1">{g.skill}</span>
      <span className={`text-xs shrink-0 ${color}`}>{hint}</span>
    </li>
  )
}

function AddToBoardDialog({
  open,
  onOpenChange,
  diagnosisId,
  defaultTitle,
  onSuccess,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  diagnosisId: string
  defaultTitle: string
  onSuccess: () => void
}) {
  // jd_title 是单字符串，无法可靠拆出公司名 → company 留空让用户填
  const [company, setCompany] = useState('')
  const [title, setTitle] = useState(defaultTitle)
  const [location, setLocation] = useState('')
  const [salary, setSalary] = useState('')
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const canSubmit = useMemo(
    () => company.trim().length > 0 && title.trim().length > 0 && !busy,
    [company, title, busy],
  )

  function reset() {
    setCompany('')
    setTitle(defaultTitle)
    setLocation('')
    setSalary('')
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
        notes: notes.trim() || null,
        diagnosis_id: diagnosisId,
      })
      onSuccess()
    } catch (e) {
      setErr((e as Error).message || '加入看板失败')
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
          <Dialog.Title className="text-lg mb-md">加入看板</Dialog.Title>
          <Dialog.Description className="sr-only">
            将本次 JD 诊断保存为岗位卡片，复用诊断结果不再调 LLM
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
              {busy ? '…' : '加入看板'}
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
