import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  deleteTarget,
  getTarget,
  regenerateTargetAdvice,
  updateTarget,
  type GapSkill,
  type JDDiagnoseResponse,
  type TargetDetail,
  type TargetStatus,
  type TargetUpdatePayload,
} from '../lib/api'

const STATUS_LABEL: Record<TargetStatus, string> = {
  interested: '感兴趣',
  applied: '已投递',
  test: '笔试',
  interview: '面试',
  offer: 'Offer',
  rejected: '被拒',
  abandoned: '放弃',
}

const STATUS_VALUES = Object.keys(STATUS_LABEL) as TargetStatus[]

const PRIORITY_HINT: Record<string, string> = {
  high: '先补这个',
  medium: '可以补',
  low: '再说',
}

export default function TargetDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [data, setData] = useState<TargetDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showJd, setShowJd] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [adviceLoading, setAdviceLoading] = useState(false)

  useEffect(() => {
    if (!id) return
    let cancelled = false
    getTarget(id)
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

  async function handlePatch(patch: TargetUpdatePayload) {
    if (!id || !data) return
    try {
      const next = await updateTarget(id, patch)
      setData(next)
    } catch (e) {
      setError((e as Error).message || '更新失败')
    }
  }

  async function handleRegenerateAdvice() {
    if (!id) return
    setAdviceLoading(true)
    setError(null)
    try {
      await regenerateTargetAdvice(id)
      const maxAttempts = 20
      const delayMs = 2000
      let last: TargetDetail | null = null
      for (let i = 0; i < maxAttempts; i++) {
        await new Promise((r) => window.setTimeout(r, delayMs))
        last = await getTarget(id)
        setData(last)
        if (last.agent_advice) break
      }
      if (last && !last.agent_advice) {
        setError('建议仍在生成中，请稍后再试或检查后端日志与 DASHSCOPE_API_KEY')
      }
    } catch (e) {
      setError((e as Error).message || '重新生成建议失败')
    } finally {
      setAdviceLoading(false)
    }
  }

  async function handleDelete() {
    if (!id) return
    if (!confirmingDelete) {
      setConfirmingDelete(true)
      window.setTimeout(() => setConfirmingDelete(false), 3000)
      return
    }
    try {
      await deleteTarget(id)
      window.location.href = '/targets'
    } catch (e) {
      setError((e as Error).message || '删除失败')
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
        <p className="text-danger">{error || '岗位不存在'}</p>
        <Link to="/targets" className="text-ink hover:text-ink-deep">
          ← 返回看板
        </Link>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[820px] px-md py-xl flex flex-col gap-lg ink-fade-in">
      <div className="flex items-center justify-between">
        <Link to="/targets" className="text-text-muted hover:text-ink text-sm">
          ← 返回看板
        </Link>
        <button
          onClick={handleDelete}
          className={[
            'text-sm transition-colors',
            confirmingDelete
              ? 'text-danger'
              : 'text-text-subtle hover:text-danger',
          ].join(' ')}
        >
          {confirmingDelete ? '确定删除?' : '删除岗位'}
        </button>
      </div>

      <header className="flex flex-col gap-2xs">
        <h2 className="text-2xl">
          {data.company} · {data.title}
        </h2>
        <div className="text-sm text-text-muted flex flex-wrap items-center gap-sm">
          {data.location ? <span>{data.location}</span> : null}
          {data.salary ? (
            <>
              <span className="text-text-subtle">·</span>
              <span>{data.salary}</span>
            </>
          ) : null}
          <span className="text-text-subtle">·</span>
          <span>{STATUS_LABEL[data.status]}</span>
          {data.status === 'interview' && data.interview_round ? (
            <>
              <span className="text-text-subtle">·</span>
              <span>{data.interview_round}</span>
            </>
          ) : null}
        </div>
      </header>

      {data.diagnosis ? <MatchAnalysis d={data.diagnosis} /> : null}

      <Section title="行动建议" right={
        <button
          type="button"
          disabled={adviceLoading}
          onClick={handleRegenerateAdvice}
          className="text-xs text-text-subtle hover:text-ink disabled:opacity-50 disabled:pointer-events-none"
        >
          {adviceLoading ? '生成中…' : '重新获取建议'}
        </button>
      }>
        {data.agent_advice ? (
          <p className="text-base text-text">{data.agent_advice}</p>
        ) : (
          <p className="text-text-subtle italic">
            {adviceLoading
              ? '正在请求 AI 生成建议，请稍候…'
              : '尚未生成。请点击右上角「重新获取建议」（首次创建后的后台任务可能因超时或开发环境重载而未完成）'}
          </p>
        )}
      </Section>

      {data.diagnosis && data.diagnosis.resume_tips.length > 0 ? (
        <Section title="简历建议">
          <ul className="flex flex-col gap-2xs">
            {data.diagnosis.resume_tips.map((t, i) => (
              <li key={i} className="flex items-baseline gap-sm text-base">
                <span className="text-text-subtle">·</span>
                <span className="flex-1">{t}</span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <StatusEditor data={data} onPatch={handlePatch} />

      {data.jd_text ? (
        <Section
          title="JD 原文"
          right={
            <button
              onClick={() => setShowJd((v) => !v)}
              className="text-xs text-text-subtle hover:text-ink"
            >
              {showJd ? '收起' : '展开'}
            </button>
          }
        >
          {showJd ? (
            <pre className="text-sm text-text-muted whitespace-pre-wrap font-han">
              {data.jd_text}
            </pre>
          ) : null}
        </Section>
      ) : null}
    </div>
  )
}

function MatchAnalysis({ d }: { d: JDDiagnoseResponse }) {
  return (
    <Section title="匹配分析">
      <div className="flex flex-col gap-sm">
        <p className="text-base">
          综合匹配：<span className="text-ink">{d.overall_score}/100</span>
        </p>
        {d.summary ? (
          <p className="text-text-muted text-sm">{d.summary}</p>
        ) : null}
        {d.strengths.length > 0 ? (
          <Sub label="优势">
            <BulletList items={d.strengths} />
          </Sub>
        ) : null}
        {d.risks.length > 0 ? (
          <Sub label="风险">
            <BulletList items={d.risks} />
          </Sub>
        ) : null}
        {d.skill_gaps.length > 0 ? (
          <Sub label="缺口">
            <ul className="flex flex-col gap-2xs">
              {d.skill_gaps.map((g, i) => (
                <GapItem key={i} g={g} />
              ))}
            </ul>
          </Sub>
        ) : null}
      </div>
    </Section>
  )
}

function StatusEditor({
  data,
  onPatch,
}: {
  data: TargetDetail
  onPatch: (p: TargetUpdatePayload) => void
}) {
  const [round, setRound] = useState(data.interview_round ?? '')
  const [notes, setNotes] = useState(data.notes ?? '')

  return (
    <Section title="投递状态">
      <div className="flex flex-col gap-sm">
        <label className="flex items-center gap-md text-sm">
          <span className="text-text-muted w-[3em]">状态</span>
          <select
            value={data.status}
            onChange={(e) =>
              onPatch({ status: e.target.value as TargetStatus })
            }
            className="bg-bg border border-border-soft rounded-sm px-sm py-2xs"
          >
            {STATUS_VALUES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        </label>

        {data.status === 'interview' ? (
          <label className="flex items-center gap-md text-sm">
            <span className="text-text-muted w-[3em]">轮次</span>
            <input
              value={round}
              onChange={(e) => setRound(e.target.value)}
              onBlur={() => {
                if ((data.interview_round ?? '') !== round) {
                  onPatch({ interview_round: round.trim() || null })
                }
              }}
              placeholder="一面 / 二面 / HR 面"
              className="flex-1 bg-bg border border-border-soft rounded-sm px-sm py-2xs"
            />
          </label>
        ) : null}

        <label className="flex flex-col gap-2xs text-sm">
          <span className="text-text-muted">备注</span>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            onBlur={() => {
              if ((data.notes ?? '') !== notes) {
                onPatch({ notes: notes.trim() || null })
              }
            }}
            rows={3}
            className="w-full bg-bg border border-border-soft rounded-sm px-sm py-2xs resize-y"
          />
        </label>
      </div>
    </Section>
  )
}

function Section({
  title,
  right,
  children,
}: {
  title: string
  right?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="flex flex-col gap-sm border border-border-soft rounded-md bg-surface p-md">
      <div className="flex items-center justify-between">
        <h3 className="text-lg">{title}</h3>
        {right}
      </div>
      {children}
    </section>
  )
}

function Sub({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-2xs">
      <span className="text-sm text-text-muted">{label}</span>
      {children}
    </div>
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
