import { useEffect, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import {
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

  useEffect(() => {
    if (!id) return
    let cancelled = false
    setLoading(true)
    setError(null)
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
        {data.jd_text ? (
          <button
            onClick={handleRediagnose}
            className="text-text-subtle hover:text-ink text-sm"
          >
            重新诊断
          </button>
        ) : null}
      </div>

      {/* 报告内容 */}
      <ReportContent data={data} />
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
