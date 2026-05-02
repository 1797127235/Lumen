import { useEffect, useRef, useState, type DragEvent, type KeyboardEvent } from 'react'
import {
  getProfile,
  patchProfile,
  resetProfile,
  uploadResume,
  type Profile,
  type SkillItem,
} from '../lib/api'

const SCHOOL_LEVEL: Array<[string, string]> = [
  ['985', '985'],
  ['211', '211'],
  ['double_first_class', '双一流'],
  ['normal', '普通本科'],
]

const GRADE: Array<[string, string]> = [
  ['freshman', '大一'],
  ['sophomore', '大二'],
  ['junior', '大三'],
  ['senior', '大四'],
  ['graduate1', '研一'],
  ['graduate2', '研二'],
  ['graduate3', '研三'],
]

const COMPANY_LEVEL: Array<[string, string]> = [
  ['top', '大厂'],
  ['major', '一线'],
  ['medium', '中厂'],
  ['state_owned', '国企'],
]

const SKILL_LEVEL: Array<[string, string]> = [
  ['beginner', '入门'],
  ['familiar', '熟练'],
  ['intermediate', '一般'],
  ['advanced', '精通'],
]

const ACCEPT_EXT = ['.pdf', '.docx', '.txt', '.md']

// ── 速写本侧栏: 章节锚 ──
const SECTIONS: Array<{ id: string; label: string }> = [
  { id: 'brief', label: '速写' },
  { id: 'timeline', label: '履历' },
  { id: 'skills', label: '技能' },
  { id: 'academic', label: '学业' },
]
const SECTION_IDS = SECTIONS.map((s) => s.id)

// ── 速写本布局的扩展字段 (v1: mock-only,后端字段后补) ──
type PortfolioLink = { label: string; url: string }

type ExtendedProfile = Profile & {
  projects?: Array<{ period: string; title: string; description: string }>
  internships?: Array<{ period: string; company: string; role: string; description: string }>
  portfolio_links?: PortfolioLink[]
  target_cities?: string[]
  expected_salary?: string | null
  english_level?: string | null
}

const MOCK_PROFILE: ExtendedProfile = {
  nickname: '小李',
  school_name: '清华大学',
  school_level: '985',
  major: '计算机科学与技术',
  grade: 'junior',
  graduation_year: 2027,
  target_direction: '后端',
  target_company_level: 'top',
  current_skills: [
    { name: 'Go', level: 'intermediate', context: '实习中常用,做了 RPC 中间件' },
    { name: 'Python', level: 'advanced', context: '课程项目主力语言' },
    { name: 'MySQL', level: 'familiar', context: '实习中常用' },
    { name: 'Redis', level: 'familiar', context: '课程项目用过' },
    { name: 'Kubernetes', level: 'beginner', context: null },
  ],
  gpa: '3.8/4.0',
  ranking: '前 15%',
  awards: ['国家奖学金 2024', 'ACM-ICPC 区域赛银牌', '蓝桥杯省一'],
  target_cities: ['北京', '上海'],
  expected_salary: '25-35k',
  english_level: 'CET-6',
  portfolio_links: [
    { label: 'github.com/xxx', url: 'https://github.com/' },
    { label: '个人博客', url: 'https://example.com/' },
  ],
  internships: [
    {
      period: '2024.06 — 现在',
      company: '字节跳动',
      role: '后端实习',
      description:
        'Go + Kitex 做内容审核中台,日均 5 亿请求.负责限流模块重写,P99 从 80ms 降到 30ms.',
    },
  ],
  projects: [
    {
      period: '2023.09 — 2024.01',
      title: '校园二手书交易平台',
      description:
        '小组 4 人项目,我负责后端.Spring Boot + MySQL + Redis,上线后注册用户 2k+.',
    },
    {
      period: '2023.03 — 2023.06',
      title: '分布式任务调度系统(课程项目)',
      description: '基于 Raft 协议自实现小型分布式调度器,Go 写的.结课最高分.',
    },
  ],
}

function isMockMode(): boolean {
  if (typeof window === 'undefined') return false
  return new URLSearchParams(window.location.search).get('mock') === '1'
}

function labelOf(table: Array<[string, string]>, key: string | null | undefined): string {
  if (!key) return ''
  return table.find(([k]) => k === key)?.[1] ?? key
}

function isFilled(p: Profile | null): boolean {
  if (!p) return false
  return Boolean(
    p.school_name ||
      p.major ||
      p.target_direction ||
      (p.current_skills && p.current_skills.length > 0),
  )
}

export default function ProfilePage() {
  const mock = isMockMode()
  const [profile, setProfile] = useState<ExtendedProfile | null>(
    mock ? MOCK_PROFILE : null,
  )
  const [loading, setLoading] = useState(!mock)

  useEffect(() => {
    if (mock) return
    getProfile()
      .then((p) => setProfile(p))
      .catch(() => setProfile(null))
      .finally(() => setLoading(false))
  }, [mock])

  async function handleUpload(file: File) {
    if (mock) {
      setProfile(MOCK_PROFILE)
      return
    }
    const res = await uploadResume(file)
    setProfile(res.profile)
  }

  async function handlePatch(patch: Partial<ExtendedProfile>) {
    if (mock) {
      setProfile((cur) => (cur ? { ...cur, ...patch } : cur))
      return
    }
    const next = await patchProfile(patch as Partial<Profile>)
    setProfile(next)
  }

  async function handleReset() {
    if (mock) {
      setProfile(null)
      return
    }
    const next = await resetProfile()
    setProfile(next)
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-[720px] px-md py-2xl">
        <div className="ink-progress mt-2xl" />
      </div>
    )
  }

  if (isFilled(profile)) {
    return (
      <FilledProfile
        profile={profile!}
        onPatch={handlePatch}
        onReset={handleReset}
      />
    )
  }

  return (
    <div className="mx-auto max-w-[720px] px-md py-2xl">
      <EmptyState onUpload={handleUpload} />
    </div>
  )
}

function EmptyState({ onUpload }: { onUpload: (f: File) => Promise<void> }) {
  return (
    <div className="flex flex-col gap-xl ink-fade-in">
      <header>
        <h2 className="text-2xl">先扔一份简历给我.</h2>
        <p className="text-text-muted text-base mt-xs">我看看你现在在哪一格.</p>
      </header>
      <UploadZone onUpload={onUpload} />
    </div>
  )
}

function FilledProfile({
  profile,
  onPatch,
  onReset,
}: {
  profile: ExtendedProfile
  onPatch: (p: Partial<ExtendedProfile>) => Promise<void>
  onReset: () => Promise<void>
}) {
  const timeline = mergeTimeline(profile)
  const activeId = useScrollSpy(SECTION_IDS, 120)

  return (
    <div className="mx-auto max-w-[1100px] px-md py-2xl grid grid-cols-1 lg:grid-cols-[160px_minmax(0,1fr)_160px] gap-x-2xl gap-y-xl ink-fade-in">
      <Spine activeId={activeId} />

      <main className="flex flex-col gap-2xl min-w-0">
        <header className="flex items-start justify-between gap-md">
          <div>
            <h2 className="text-2xl">这是你现在的样子.</h2>
            <p className="text-text-muted text-base mt-xs">从你上传的简历里读出来的.</p>
          </div>
          <ResetButton onReset={onReset} />
        </header>

      <Divider />

      <Section caption="速写" id="brief">
        <BriefRow>
          <BriefField
            primary
            display={profile.school_name || '学校'}
            value={profile.school_name}
            onSave={(v) => onPatch({ school_name: v || null })}
            placeholder="学校"
            inputClass="w-56"
          />
          <BriefSep />
          <BriefField
            primary
            display={profile.major || '专业'}
            value={profile.major}
            onSave={(v) => onPatch({ major: v || null })}
            placeholder="专业"
            inputClass="w-64"
          />
        </BriefRow>

        <BriefRow>
          <BriefSelect
            display={
              profile.school_level
                ? labelOf(SCHOOL_LEVEL, profile.school_level)
                : '学校层级'
            }
            value={profile.school_level}
            options={SCHOOL_LEVEL}
            onSave={(v) => onPatch({ school_level: v || null })}
          />
          <BriefSep />
          <BriefSelect
            display={profile.grade ? labelOf(GRADE, profile.grade) : '年级'}
            value={profile.grade}
            options={GRADE}
            onSave={(v) => onPatch({ grade: v || null })}
          />
          <BriefSep />
          <BriefField
            display={
              profile.graduation_year ? `${profile.graduation_year} 毕业` : '毕业年'
            }
            value={
              profile.graduation_year ? String(profile.graduation_year) : ''
            }
            onSave={(v) => {
              const n = Number.parseInt(v, 10)
              return onPatch({
                graduation_year: Number.isFinite(n) ? n : null,
              })
            }}
            placeholder="2027"
            inputType="number"
            inputClass="w-24"
          />
          <BriefSep />
          <BriefField
            display={profile.target_direction || '方向'}
            value={profile.target_direction}
            onSave={(v) => onPatch({ target_direction: v || null })}
            placeholder="后端 / AI"
            inputClass="w-28"
          />
          <BriefSep />
          <BriefSelect
            display={
              profile.target_company_level
                ? labelOf(COMPANY_LEVEL, profile.target_company_level)
                : '公司层级'
            }
            value={profile.target_company_level}
            options={COMPANY_LEVEL}
            onSave={(v) => onPatch({ target_company_level: v || null })}
          />
        </BriefRow>

        <BriefRow>
          <BriefField
            display={
              profile.target_cities && profile.target_cities.length
                ? profile.target_cities.join(' / ')
                : '城市'
            }
            value={profile.target_cities?.join(', ') ?? ''}
            onSave={(v) => {
              const cities = v
                .split(/[,,\s/]+/)
                .map((s) => s.trim())
                .filter(Boolean)
              return onPatch({
                target_cities: cities.length ? cities : undefined,
              })
            }}
            placeholder="北京, 上海"
            inputClass="w-40"
          />
          <BriefSep />
          <BriefField
            display={profile.english_level || '英语'}
            value={profile.english_level}
            onSave={(v) => onPatch({ english_level: v || null })}
            placeholder="CET-6"
            inputClass="w-28"
          />
          <BriefSep />
          <BriefField
            display={profile.expected_salary || '期望薪资'}
            value={profile.expected_salary}
            onSave={(v) => onPatch({ expected_salary: v || null })}
            placeholder="20-30k"
            inputClass="w-28"
          />
        </BriefRow>

        <PortfolioLine
          links={profile.portfolio_links ?? []}
          onChange={(links) =>
            onPatch({
              portfolio_links: links.length ? links : undefined,
            })
          }
        />
      </Section>

      <Divider />

      <Section caption="履历" id="timeline">
        {timeline.length === 0 ? (
          <p className="text-text-muted text-sm">还没看到项目和实习经历.</p>
        ) : (
          <div className="flex flex-col gap-lg">
            {timeline.map((e, i) => (
              <TimelineEntry key={`${e.period}-${i}`} {...e} />
            ))}
          </div>
        )}
      </Section>

      <Section caption="技能" id="skills">
        <SkillList
          skills={profile.current_skills ?? []}
          onChange={(skills) => onPatch({ current_skills: skills })}
        />
      </Section>

      <Section caption="学业" id="academic">
        <BriefRow>
          <BriefField
            display={profile.gpa ? `GPA ${profile.gpa}` : 'GPA'}
            value={profile.gpa}
            onSave={(v) => onPatch({ gpa: v || null })}
            placeholder="3.8/4.0"
            inputClass="w-24"
          />
          <BriefSep />
          <BriefField
            display={profile.ranking ? `排名 ${profile.ranking}` : '排名'}
            value={profile.ranking}
            onSave={(v) => onPatch({ ranking: v || null })}
            placeholder="前10%"
            inputClass="w-24"
          />
        </BriefRow>
        <AwardList
          awards={profile.awards ?? []}
          onChange={(awards) =>
            onPatch({ awards: awards.length ? awards : null })
          }
        />
      </Section>
      </main>

      <Aside profile={profile} />
    </div>
  )
}

// ── 速写本布局: brief 行内字段 ──

function BriefRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-sm gap-y-2xs">
      {children}
    </div>
  )
}

function BriefSep() {
  return <span className="text-text-subtle select-none">·</span>
}

function BriefField({
  display,
  value,
  onSave,
  placeholder,
  inputClass,
  inputType,
  primary,
}: {
  display: string
  value: string | null | undefined
  onSave: (v: string) => Promise<void> | void
  placeholder?: string
  inputClass?: string
  inputType?: 'text' | 'number'
  primary?: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)

  function start() {
    setDraft(value != null ? String(value) : '')
    setEditing(true)
  }
  function cancel() {
    setEditing(false)
    setDraft('')
  }
  async function commit() {
    if (busy) return
    setBusy(true)
    try {
      await onSave(draft.trim())
      setEditing(false)
      setDraft('')
    } finally {
      setBusy(false)
    }
  }
  function onKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      commit()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      cancel()
    }
  }

  if (editing) {
    return (
      <span className="inline-flex items-baseline gap-2xs">
        <input
          autoFocus
          type={inputType ?? 'text'}
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          className={[
            'bg-transparent border-b border-ink py-2xs px-0 outline-none',
            primary ? 'text-lg' : 'text-base',
            inputClass ?? '',
          ].join(' ')}
        />
        <span className="text-xs text-text-subtle whitespace-nowrap font-latin">
          ↵
        </span>
      </span>
    )
  }

  const filled = value != null && String(value).length > 0
  return (
    <button
      onClick={start}
      className={[
        'border-b border-dashed border-transparent hover:border-border-soft transition-colors text-left',
        primary ? 'text-lg' : 'text-base',
        filled
          ? primary
            ? 'text-text hover:text-ink'
            : 'text-text-muted hover:text-ink'
          : 'text-text-subtle italic hover:text-text-muted',
      ].join(' ')}
    >
      {display}
    </button>
  )
}

function BriefSelect({
  display,
  value,
  options,
  onSave,
}: {
  display: string
  value: string | null | undefined
  options: Array<[string, string]>
  onSave: (v: string) => Promise<void> | void
}) {
  const [editing, setEditing] = useState(false)

  if (editing) {
    return (
      <select
        autoFocus
        value={value ?? ''}
        onChange={async (e) => {
          const v = e.target.value
          setEditing(false)
          await onSave(v)
        }}
        onBlur={() => setEditing(false)}
        className="text-base bg-bg border-b border-ink py-2xs"
      >
        <option value="">—</option>
        {options.map(([k, label]) => (
          <option key={k} value={k}>
            {label}
          </option>
        ))}
      </select>
    )
  }

  const filled = Boolean(value)
  return (
    <button
      onClick={() => setEditing(true)}
      className={[
        'border-b border-dashed border-transparent hover:border-border-soft transition-colors text-base',
        filled
          ? 'text-text-muted hover:text-ink'
          : 'text-text-subtle italic hover:text-text-muted',
      ].join(' ')}
    >
      {display}
    </button>
  )
}

function PortfolioLine({
  links,
  onChange,
}: {
  links: PortfolioLink[]
  onChange: (links: PortfolioLink[]) => Promise<void> | void
}) {
  const [adding, setAdding] = useState(false)
  const [label, setLabel] = useState('')
  const [url, setUrl] = useState('')

  async function add() {
    const u = url.trim()
    if (!u) return
    await onChange([...links, { label: label.trim() || u, url: u }])
    setLabel('')
    setUrl('')
    setAdding(false)
  }
  async function remove(idx: number) {
    await onChange(links.filter((_, i) => i !== idx))
  }
  function onKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      add()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setAdding(false)
      setLabel('')
      setUrl('')
    }
  }

  return (
    <BriefRow>
      {links.length === 0 && !adding ? (
        <span className="text-text-subtle italic text-base select-none">
          还没填作品集
        </span>
      ) : null}
      {links.map((l, i) => (
        <span key={i} className="group inline-flex items-baseline">
          {i > 0 ? <BriefSep /> : null}
          <a
            href={l.url}
            target="_blank"
            rel="noreferrer noopener"
            className="ml-2xs text-base text-ink hover:text-ink-deep underline underline-offset-4 decoration-dashed decoration-border-soft hover:decoration-ink"
          >
            {l.label}
          </a>
          <button
            onClick={() => remove(i)}
            className="ml-2xs text-xs text-text-subtle opacity-0 group-hover:opacity-100 transition-opacity hover:text-danger"
            aria-label="删除链接"
          >
            ×
          </button>
        </span>
      ))}
      {adding ? (
        <span
          className="inline-flex items-baseline gap-xs"
          onKeyDown={onKeyDown}
        >
          <input
            autoFocus
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="名字"
            className="text-base bg-transparent border-b border-ink py-2xs px-0 outline-none w-20"
          />
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://..."
            className="text-base bg-transparent border-b border-ink py-2xs px-0 outline-none w-56"
          />
          <button
            onClick={add}
            className="text-xs text-text-subtle hover:text-ink"
          >
            记下
          </button>
        </span>
      ) : (
        <button
          onClick={() => setAdding(true)}
          className="text-sm text-text-subtle hover:text-ink"
        >
          + 加链接
        </button>
      )}
    </BriefRow>
  )
}

// ── 履历: timeline 卡片 (A1 时间戳浮空: 左戳右文,无边框) ──

type TimelineItem = {
  period: string
  title: string
  subtitle?: string
  description: string
  sortKey: number
}

function mergeTimeline(profile: ExtendedProfile): TimelineItem[] {
  const items: TimelineItem[] = []
  for (const p of profile.projects ?? []) {
    items.push({
      period: p.period,
      title: p.title,
      description: p.description,
      sortKey: parsePeriodStart(p.period),
    })
  }
  for (const i of profile.internships ?? []) {
    items.push({
      period: i.period,
      title: i.company,
      subtitle: i.role,
      description: i.description,
      sortKey: parsePeriodStart(i.period),
    })
  }
  return items.sort((a, b) => b.sortKey - a.sortKey)
}

function parsePeriodStart(period: string): number {
  const m = period.match(/(\d{4})[.\-/](\d{1,2})/)
  if (!m) return 0
  return Number(m[1]) * 100 + Number(m[2])
}

function TimelineEntry({
  period,
  title,
  subtitle,
  description,
}: {
  period: string
  title: string
  subtitle?: string
  description: string
}) {
  return (
    <article className="grid grid-cols-[max-content_1fr] gap-x-lg gap-y-2xs">
      <time className="text-xs text-text-subtle font-latin tracking-wide pt-2xs whitespace-nowrap">
        {period}
      </time>
      <div className="flex flex-col gap-2xs">
        <h4 className="text-base">
          <span className="text-ink">{title}</span>
          {subtitle ? (
            <span className="text-text-subtle ml-sm text-sm">/ {subtitle}</span>
          ) : null}
        </h4>
        <p className="text-sm text-text-muted leading-relaxed">{description}</p>
      </div>
    </article>
  )
}

function Divider() {
  return <div className="h-px w-full bg-border-soft" />
}

function ResetButton({ onReset }: { onReset: () => Promise<void> }) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!confirming) return
    const t = window.setTimeout(() => setConfirming(false), 3000)
    return () => window.clearTimeout(t)
  }, [confirming])

  async function handleClick() {
    if (busy) return
    if (!confirming) {
      setConfirming(true)
      return
    }
    setBusy(true)
    try {
      await onReset()
    } catch {
      setBusy(false)
      setConfirming(false)
    }
  }

  return (
    <button
      onClick={handleClick}
      disabled={busy}
      className={[
        'text-xs whitespace-nowrap transition-colors shrink-0 mt-xs',
        confirming ? 'text-danger' : 'text-text-subtle hover:text-ink',
      ].join(' ')}
    >
      {busy ? '正在清空...' : confirming ? '确定清空? 不可恢复' : '重新开始'}
    </button>
  )
}

function Section({
  caption,
  children,
  id,
}: {
  caption: string
  children: React.ReactNode
  id?: string
}) {
  return (
    <section id={id} className="flex flex-col gap-sm scroll-mt-24">
      <div className="text-xs text-text-subtle uppercase tracking-wider font-latin">
        {caption}
      </div>
      <div className="flex flex-col gap-xs">{children}</div>
    </section>
  )
}

// ── 速写本侧栏 ──

function Spine({ activeId }: { activeId: string }) {
  function jump(id: string) {
    const el = document.getElementById(id)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <aside className="hidden lg:flex flex-col sticky top-[80px] self-start h-fit pt-md">
      <div
        className="text-text-muted text-base font-han tracking-[0.5em] mb-xl select-none"
        style={{ writingMode: 'vertical-rl' }}
      >
        速写本
      </div>
      <div className="h-px w-8 bg-border-soft mb-xl" />
      <nav className="flex flex-col gap-md" aria-label="章节">
        {SECTIONS.map(({ id, label }) => {
          const active = activeId === id
          return (
            <button
              key={id}
              onClick={() => jump(id)}
              className="flex items-center gap-md text-left group"
            >
              <span
                aria-hidden
                className={[
                  'h-px transition-all duration-300',
                  active ? 'w-6 bg-ink' : 'w-3 bg-border-soft group-hover:w-5 group-hover:bg-text-muted',
                ].join(' ')}
              />
              <span
                className={[
                  'text-base transition-colors',
                  active ? 'text-ink' : 'text-text-muted group-hover:text-text',
                ].join(' ')}
              >
                {label}
              </span>
            </button>
          )
        })}
      </nav>
    </aside>
  )
}

function Aside({ profile }: { profile: ExtendedProfile }) {
  const { pct, missing } = computeCompleteness(profile)
  return (
    <aside className="hidden lg:flex flex-col sticky top-[80px] self-start h-fit pt-md text-right">
      <div className="text-xs text-text-subtle uppercase tracking-wider font-latin mb-2xs">
        完整度
      </div>
      <div className="text-3xl font-latin text-ink leading-none">{pct}%</div>
      <div className="h-px w-12 bg-border-soft mt-md mb-md ml-auto" />
      {missing.length > 0 ? (
        <p className="text-xs text-text-subtle leading-relaxed">
          还差 {missing.slice(0, 2).join(' · ')}
          {missing.length > 2 ? ` 等 ${missing.length - 2}+` : null}
        </p>
      ) : (
        <p className="text-xs text-text-subtle leading-relaxed">填得满了.</p>
      )}
    </aside>
  )
}

function useScrollSpy(ids: string[], offset: number = 100): string {
  const [active, setActive] = useState<string>(ids[0] ?? '')

  useEffect(() => {
    if (typeof window === 'undefined' || ids.length === 0) return
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
        if (visible.length > 0) {
          setActive(visible[0].target.id)
        }
      },
      {
        rootMargin: `-${offset}px 0px -55% 0px`,
        threshold: 0,
      },
    )
    const els: Element[] = []
    ids.forEach((id) => {
      const el = document.getElementById(id)
      if (el) {
        observer.observe(el)
        els.push(el)
      }
    })
    return () => {
      els.forEach((el) => observer.unobserve(el))
      observer.disconnect()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids.join(','), offset])

  return active
}

function computeCompleteness(p: ExtendedProfile): {
  pct: number
  missing: string[]
} {
  const fields: Array<[string, boolean]> = [
    ['学校', !!p.school_name],
    ['专业', !!p.major],
    ['年级', !!p.grade],
    ['毕业年', !!p.graduation_year],
    ['方向', !!p.target_direction],
    ['公司层级', !!p.target_company_level],
    ['城市', Array.isArray(p.target_cities) && p.target_cities.length > 0],
    ['英语', !!p.english_level],
    ['期望薪资', !!p.expected_salary],
    [
      '作品',
      Array.isArray(p.portfolio_links) && p.portfolio_links.length > 0,
    ],
    [
      '项目/实习',
      (Array.isArray(p.projects) && p.projects.length > 0) ||
        (Array.isArray(p.internships) && p.internships.length > 0),
    ],
    [
      '技能',
      Array.isArray(p.current_skills) && p.current_skills.length > 0,
    ],
    ['GPA', !!p.gpa],
    ['排名', !!p.ranking],
    ['获奖', Array.isArray(p.awards) && p.awards.length > 0],
  ]
  const filled = fields.filter(([, v]) => v).length
  const pct = Math.round((filled / fields.length) * 100)
  const missing = fields.filter(([, v]) => !v).map(([n]) => n)
  return { pct, missing }
}

function SelectInput({
  value,
  onChange,
  options,
}: {
  value: string
  onChange: (v: string) => void
  options: Array<[string, string]>
}) {
  return (
    <select
      autoFocus
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="text-base bg-bg border-b border-border-soft focus:border-ink py-2xs pr-md"
    >
      <option value="">—</option>
      {options.map(([k, label]) => (
        <option key={k} value={k}>
          {label}
        </option>
      ))}
    </select>
  )
}

function AwardList({
  awards,
  onChange,
}: {
  awards: string[]
  onChange: (a: string[]) => Promise<void> | void
}) {
  const [adding, setAdding] = useState(false)
  const [text, setText] = useState('')

  async function add() {
    const t = text.trim()
    if (!t) return
    await onChange([...awards, t])
    setText('')
    setAdding(false)
  }
  async function remove(idx: number) {
    await onChange(awards.filter((_, i) => i !== idx))
  }
  function onKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      add()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setAdding(false)
      setText('')
    }
  }

  return (
    <div className="flex flex-col gap-2xs mt-xs">
      {awards.map((a, i) => (
        <div key={i} className="group flex items-baseline gap-md">
          <div className="text-base flex-1">
            <span className="text-text-subtle">·</span> {a}
          </div>
          <button
            onClick={() => remove(i)}
            className="text-xs text-text-subtle opacity-0 group-hover:opacity-100 transition-opacity hover:text-danger"
          >
            划掉
          </button>
        </div>
      ))}
      {adding ? (
        <div className="flex flex-col gap-2xs" onKeyDown={onKeyDown}>
          <input
            autoFocus
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="写一项获奖"
            className="w-full text-base border-b border-border-soft focus:border-ink py-2xs"
          />
          <p className="text-xs text-text-subtle">Enter 记下来 · Esc 再想想</p>
        </div>
      ) : (
        <button
          onClick={() => setAdding(true)}
          className="text-base text-ink hover:text-ink-deep self-start"
        >
          + 写写获奖
        </button>
      )}
    </div>
  )
}

function SkillList({
  skills,
  onChange,
}: {
  skills: SkillItem[]
  onChange: (s: SkillItem[]) => Promise<void> | void
}) {
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [level, setLevel] = useState('familiar')

  async function remove(idx: number) {
    const next = skills.filter((_, i) => i !== idx)
    await onChange(next)
  }
  async function add() {
    const trimmed = name.trim()
    if (!trimmed) return
    await onChange([...skills, { name: trimmed, level }])
    setName('')
    setLevel('familiar')
    setAdding(false)
  }
  async function changeLevel(idx: number, lv: string) {
    const next = skills.map((s, i) => (i === idx ? { ...s, level: lv } : s))
    await onChange(next)
  }
  async function changeContext(idx: number, ctx: string | null) {
    const next = skills.map((s, i) => (i === idx ? { ...s, context: ctx } : s))
    await onChange(next)
  }
  function onKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      add()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setAdding(false)
      setName('')
    }
  }

  return (
    <div className="flex flex-col gap-2xs">
      {skills.map((s, i) => (
        <SkillRow
          key={`${s.name}-${i}`}
          skill={s}
          onLevel={(lv) => changeLevel(i, lv)}
          onContext={(ctx) => changeContext(i, ctx)}
          onRemove={() => remove(i)}
        />
      ))}
      {adding ? (
        <div className="flex flex-col gap-2xs mt-xs" onKeyDown={onKeyDown}>
          <div className="flex items-center gap-md">
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="技能名"
              className="flex-1 text-base border-b border-border-soft focus:border-ink py-2xs"
            />
            <span className="text-text-subtle">·</span>
            <SelectInput value={level} onChange={setLevel} options={SKILL_LEVEL} />
          </div>
          <p className="text-xs text-text-subtle">Enter 记下来 · Esc 再想想</p>
        </div>
      ) : (
        <button
          onClick={() => setAdding(true)}
          className="text-base text-ink hover:text-ink-deep self-start mt-xs"
        >
          + 添加一项
        </button>
      )}
    </div>
  )
}

function SkillRow({
  skill,
  onLevel,
  onContext,
  onRemove,
}: {
  skill: SkillItem
  onLevel: (lv: string) => void
  onContext: (ctx: string | null) => void
  onRemove: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [editingContext, setEditingContext] = useState(false)
  const [ctxDraft, setCtxDraft] = useState(skill.context ?? '')

  if (editing) {
    return (
      <div className="flex items-center gap-md">
        <span className="text-base">{skill.name}</span>
        <span className="text-text-subtle">·</span>
        <SelectInput
          value={skill.level}
          onChange={(lv) => {
            onLevel(lv)
            setEditing(false)
          }}
          options={SKILL_LEVEL}
        />
        <button
          onClick={() => setEditing(false)}
          className="text-xs text-text-subtle hover:text-text"
        >
          再想想
        </button>
      </div>
    )
  }

  if (editingContext) {
    function commitCtx() {
      const trimmed = ctxDraft.trim()
      onContext(trimmed ? trimmed : null)
      setEditingContext(false)
    }
    return (
      <div className="flex items-center gap-md">
        <span className="text-base">{skill.name}</span>
        <span className="text-text-subtle">·</span>
        <span className="text-text-muted">{labelOf(SKILL_LEVEL, skill.level)}</span>
        <span className="text-text-subtle">·</span>
        <input
          autoFocus
          value={ctxDraft}
          onChange={(e) => setCtxDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              commitCtx()
            } else if (e.key === 'Escape') {
              e.preventDefault()
              setCtxDraft(skill.context ?? '')
              setEditingContext(false)
            }
          }}
          placeholder="课程项目用过 / 实习中常用…"
          className="flex-1 text-base border-b border-border-soft focus:border-ink py-2xs"
        />
        <button
          onClick={commitCtx}
          className="text-xs text-text-subtle hover:text-ink"
        >
          记下来
        </button>
        <button
          onClick={() => {
            setCtxDraft(skill.context ?? '')
            setEditingContext(false)
          }}
          className="text-xs text-text-subtle hover:text-text"
        >
          再想想
        </button>
      </div>
    )
  }

  return (
    <div className="group flex items-baseline gap-md">
      <div className="text-base border-b border-dashed border-border-soft/40 group-hover:border-border-soft transition-colors">
        {skill.name} <span className="text-text-subtle">·</span>{' '}
        <span className="text-text-muted">{labelOf(SKILL_LEVEL, skill.level)}</span>
        {skill.context ? (
          <>
            {' '}
            <span className="text-text-subtle">·</span>{' '}
            <span className="text-text-muted">{skill.context}</span>
          </>
        ) : null}
      </div>
      <button
        onClick={() => setEditing(true)}
        className="text-xs text-text-subtle opacity-0 group-hover:opacity-100 transition-opacity hover:text-ink"
      >
        改一改
      </button>
      <button
        onClick={() => {
          setCtxDraft(skill.context ?? '')
          setEditingContext(true)
        }}
        className="text-xs text-text-subtle opacity-0 group-hover:opacity-100 transition-opacity hover:text-ink"
      >
        {skill.context ? '改场景' : '+ 加场景'}
      </button>
      <button
        onClick={onRemove}
        className="text-xs text-text-subtle opacity-0 group-hover:opacity-100 transition-opacity hover:text-danger"
      >
        划掉这条
      </button>
    </div>
  )
}

function UploadZone({
  onUpload,
}: {
  onUpload: (f: File) => Promise<void>
}) {
  const ref = useRef<HTMLInputElement | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [drag, setDrag] = useState(false)

  function pick() {
    ref.current?.click()
  }

  async function handleFile(file: File) {
    setError(null)
    const ext = '.' + (file.name.split('.').pop() ?? '').toLowerCase()
    if (!ACCEPT_EXT.includes(ext)) {
      setError('这种文件我读不来.要 PDF / DOCX / TXT / MD.')
      return
    }
    if (file.size > 10 * 1024 * 1024) {
      setError('文件有点大,精简到 10MB 以内?')
      return
    }
    setBusy(true)
    try {
      await onUpload(file)
    } catch (e) {
      setError((e as Error).message || '我刚才走神了,你再传一遍.')
    } finally {
      setBusy(false)
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setDrag(false)
    const f = e.dataTransfer.files?.[0]
    if (f) handleFile(f)
  }

  return (
    <div className="flex flex-col gap-sm">
      <div
        role="button"
        tabIndex={0}
        onClick={pick}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            pick()
          }
        }}
        onDrop={onDrop}
        onDragOver={(e) => {
          e.preventDefault()
          setDrag(true)
        }}
        onDragLeave={() => setDrag(false)}
        className={[
          'flex flex-col items-center justify-center gap-2xs px-md py-lg',
          'min-h-[180px]',
          'border border-dashed cursor-pointer transition-colors',
          drag ? 'border-ink bg-surface' : 'border-border-soft hover:border-border',
          busy ? 'opacity-60 pointer-events-none' : '',
        ].join(' ')}
      >
        <p className="text-base text-text">
          拖一份简历过来
        </p>
        <p className="text-sm text-text-muted">或点击选个文件</p>
        <p className="text-xs text-text-subtle font-latin tracking-wide">
          PDF · DOCX · TXT · MD
        </p>
        <input
          ref={ref}
          type="file"
          accept={ACCEPT_EXT.join(',')}
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) handleFile(f)
            e.target.value = ''
          }}
        />
      </div>
      {busy ? (
        <div className="flex flex-col gap-xs">
          <p className="text-text-muted text-sm">我在读...</p>
          <div className="ink-progress" />
        </div>
      ) : null}
      {error ? <p className="text-danger text-sm">{error}</p> : null}
    </div>
  )
}
