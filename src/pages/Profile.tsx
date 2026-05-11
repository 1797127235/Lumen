import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getStructuredProfile,
  getMemoryContent,
  resetMemory,
  updateStructuredProfile,
  uploadResume,
  getAIUnderstanding,
  refreshAIUnderstanding,
  correctAIUnderstanding,
  type StructuredProfile,
  type ProfileUpdatePayload,
  type AboutYouResponse,
} from '../lib/api'

// ═══════════════════════════════════════════
//  类型与常量
// ═══════════════════════════════════════════

type ProfileData = StructuredProfile

const SKILL_LEVELS = ['beginner', 'familiar', 'intermediate', 'advanced', 'expert'] as const
type SkillLevel = (typeof SKILL_LEVELS)[number]

const LEVEL_COLORS: Record<string, string> = {
  beginner: 'text-text-subtle border-text-subtle/30',
  familiar: 'text-text-muted border-text-muted/40',
  intermediate: 'text-ink-soft border-ink-soft/40',
  advanced: 'text-ink border-ink/50',
  expert: 'text-ink-deep border-ink-deep/60',
}

const LEVEL_LABELS: Record<string, string> = {
  beginner: '入门',
  familiar: '了解',
  intermediate: '熟练',
  advanced: '精通',
  expert: '专家',
}

const PATTERN_ICONS: Record<string, string> = {
  time_preference: '🌙',
  learning_style: '📚',
  decision_pattern: '🎯',
  value_orientation: '💎',
  communication_style: '💬',
  default: '💡',
}

// 基础信息字段定义
const PROFILE_FIELDS: Array<{ key: string; label: string; placeholder: string; type?: 'text' | 'textarea' | 'select'; options?: string[] }> = [
  { key: 'school_name', label: '学校', placeholder: '例如：清华大学' },
  { key: 'major', label: '专业', placeholder: '例如：计算机科学' },
  { key: 'grade', label: '年级', placeholder: '例如：大三', type: 'select', options: ['大一', '大二', '大三', '大四', '研一', '研二', '研三', '已毕业'] },
  { key: 'graduation_year', label: '毕业年份', placeholder: '例如：2025' },
  { key: 'school_level', label: '学校层次', placeholder: '例如：985/211', type: 'select', options: ['985', '211', '双一流', '普通本科', '专科', '海外院校'] },
  { key: 'target_direction', label: '目标方向', placeholder: '例如：后端开发 / AI算法' },
  { key: 'target_company_level', label: '目标公司', placeholder: '例如：大厂 / 中厂', type: 'select', options: ['大厂', '中厂', '小厂', '创业公司', '外企', '国企', '无所谓'] },
  { key: 'city', label: '意向城市', placeholder: '例如：北京 / 上海' },
  { key: 'gpa', label: 'GPA', placeholder: '例如：3.8/4.0' },
  { key: 'ranking', label: '排名', placeholder: '例如：前 10%' },
  { key: 'english_level', label: '英语水平', placeholder: '例如：CET-6 / 雅思7.0', type: 'select', options: ['无', 'CET-4', 'CET-6', '雅思', '托福', '专八'] },
  { key: 'expected_salary', label: '期望薪资', placeholder: '例如：20k-30k' },
  { key: 'bio', label: '个人简介', placeholder: '简短介绍自己...', type: 'textarea' },
]

// ═══════════════════════════════════════════
//  工具函数
// ═══════════════════════════════════════════

function formatDate(dateStr: string | null): string {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr)
    return d.toLocaleDateString('zh-CN')
  } catch {
    return dateStr
  }
}

function formatRelativeTime(dateStr: string | null): string {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr)
    const now = new Date()
    const diffMs = now.getTime() - d.getTime()
    const diffMins = Math.floor(diffMs / 60000)
    if (diffMins < 1) return '刚刚'
    if (diffMins < 60) return `${diffMins} 分钟前`
    const diffHours = Math.floor(diffMins / 60)
    if (diffHours < 24) return `${diffHours} 小时前`
    const diffDays = Math.floor(diffHours / 24)
    if (diffDays < 30) return `${diffDays} 天前`
    return d.toLocaleDateString('zh-CN')
  } catch {
    return dateStr
  }
}

function isEmptyProfile(profile: Record<string, unknown>): boolean {
  return Object.keys(profile).length === 0
}

function getProfileField(profile: Record<string, unknown>, key: string): string {
  const val = profile[key]
  if (val === null || val === undefined) return ''
  if (Array.isArray(val)) return val.join(', ')
  return String(val)
}

function _categoryIcon(category: string): string {
  return PATTERN_ICONS[category] || PATTERN_ICONS.default
}

// ═══════════════════════════════════════════
//  子组件：卡片外壳
// ═══════════════════════════════════════════

function Card({
  title,
  icon,
  children,
  action,
  className = '',
}: {
  title: string
  icon?: string
  children: React.ReactNode
  action?: React.ReactNode
  className?: string
}) {
  return (
    <div className={`border border-border-soft rounded-xl bg-surface overflow-hidden ${className}`}>
      <div className="flex items-center justify-between px-md py-sm border-b border-border-soft">
        <div className="flex items-center gap-xs">
          {icon && <span className="text-lg">{icon}</span>}
          <h2 className="text-base font-medium text-text">{title}</h2>
        </div>
        {action}
      </div>
      <div className="p-md">{children}</div>
    </div>
  )
}

function EditButton({ onClick, editing }: { onClick: () => void; editing?: boolean }) {
  return (
    <button
      onClick={onClick}
      className="text-xs text-ink hover:text-ink-deep transition-colors px-sm py-1 rounded-md hover:bg-ink/10"
    >
      {editing ? '取消' : '编辑'}
    </button>
  )
}

function SaveButton({ onClick, loading }: { onClick: () => void; loading?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={loading}
      className="text-xs bg-ink text-bg px-sm py-1 rounded-md hover:bg-ink-deep disabled:opacity-50 transition-colors"
    >
      {loading ? '保存中…' : '保存'}
    </button>
  )
}

// ═══════════════════════════════════════════
//  子组件：空状态
// ═══════════════════════════════════════════

function EmptyState({ message, hint }: { message: string; hint?: string }) {
  return (
    <div className="text-center py-xl">
      <p className="text-text-muted text-sm">{message}</p>
      {hint && <p className="text-text-subtle text-xs mt-xs">{hint}</p>}
    </div>
  )
}

// ═══════════════════════════════════════════
//  主页面
// ═══════════════════════════════════════════

export default function ProfilePage() {
  const [data, setData] = useState<ProfileData | null>(null)
  const [understanding, setUnderstanding] = useState<AboutYouResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // 编辑状态
  const [editingProfile, setEditingProfile] = useState(false)
  const [editingSkills, setEditingSkills] = useState(false)
  const [editingExperiences, setEditingExperiences] = useState(false)

  // 关于你编辑状态
  const [editingAboutYou, setEditingAboutYou] = useState(false)
  const [editAboutYouText, setEditAboutYouText] = useState('')

  // 简历数据折叠
  const [showResumeData, setShowResumeData] = useState(false)

  // 本地编辑数据
  const [editProfile, setEditProfile] = useState<Record<string, unknown>>({})
  const [editSkills, setEditSkills] = useState<Array<{ name: string; level: string; context: string }>>([])
  const [editExperiences, setEditExperiences] = useState<Array<{ title: string; description: string; period: string; tech_stack: string; role: string }>>([])

  // 上传
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [under, profile] = await Promise.all([
        getAIUnderstanding(),
        getStructuredProfile(),
      ])
      setUnderstanding(under)
      setData(profile)
    } catch (e) {
      setError((e as Error).message || '加载失败')
      // 降级到旧 API
      try {
        const old = await getMemoryContent()
        setUnderstanding({ about_you: '', updated_at: '', patterns: [], now_status: {}, journey: [] })
        setData({
          profile: {},
          skills: [],
          experiences: [],
          goals: {},
          preferences: {},
          status: {},
          decisions: [],
        })
      } catch {
        // ignore
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  // ── 保存 Profile ──
  async function saveProfile() {
    setBusy(true)
    try {
      const payload: ProfileUpdatePayload = { profile: editProfile }
      await updateStructuredProfile(payload)
      setEditingProfile(false)
      await loadData()
    } catch (e) {
      setError((e as Error).message || '保存失败')
    } finally {
      setBusy(false)
    }
  }

  // ── 保存 Skills ──
  async function saveSkills() {
    setBusy(true)
    try {
      const payload: ProfileUpdatePayload = {
        skills: editSkills.map((s) => ({ name: s.name, level: s.level, context: s.context })),
      }
      await updateStructuredProfile(payload)
      setEditingSkills(false)
      await loadData()
    } catch (e) {
      setError((e as Error).message || '保存失败')
    } finally {
      setBusy(false)
    }
  }

  // ── 保存 Experiences ──
  async function saveExperiences() {
    setBusy(true)
    try {
      const payload: ProfileUpdatePayload = {
        experiences: editExperiences.map((e) => ({
          title: e.title,
          description: e.description,
          period: e.period,
          tech_stack: e.tech_stack,
          role: e.role,
        })),
      }
      await updateStructuredProfile(payload)
      setEditingExperiences(false)
      await loadData()
    } catch (e) {
      setError((e as Error).message || '保存失败')
    } finally {
      setBusy(false)
    }
  }

  // ── 重置 ──
  async function handleReset() {
    if (busy) return
    if (!confirm('确定要重置所有记忆吗？这会清空你的画像、技能和经历。')) return
    setBusy(true)
    setError(null)
    try {
      await resetMemory()
      await loadData()
    } catch (e) {
      setError((e as Error).message || '重置失败')
    } finally {
      setBusy(false)
    }
  }

  // ── 刷新画像 ──
  async function handleRefreshAboutYou() {
    setBusy(true)
    try {
      await refreshAIUnderstanding()
      await loadData()
    } catch (e) {
      setError((e as Error).message || '刷新失败')
    } finally {
      setBusy(false)
    }
  }

  // ── 纠正画像 ──
  function handleStartCorrection() {
    setEditAboutYouText(understanding?.about_you || '')
    setEditingAboutYou(true)
  }

  async function handleSaveCorrection() {
    setBusy(true)
    try {
      await correctAIUnderstanding(editAboutYouText)
      setEditingAboutYou(false)
      await loadData()
    } catch (e) {
      setError((e as Error).message || '保存失败')
    } finally {
      setBusy(false)
    }
  }

  // ── 上传简历 ──
  const handleUpload = useCallback(async (file: File) => {
    setUploading(true)
    setError(null)
    try {
      await uploadResume(file)
      await loadData()
    } catch (e) {
      setError((e as Error).message || '上传失败')
    } finally {
      setUploading(false)
    }
  }, [loadData])

  const onPageDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    if (!e.dataTransfer.types.includes('Files')) return
    setDragOver(true)
  }, [])

  const onPageDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    const next = e.relatedTarget as Node | null
    if (next && (e.currentTarget as HTMLElement).contains(next)) return
    setDragOver(false)
  }, [])

  const onPageDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    if (e.dataTransfer.types.includes('Files')) {
      e.dataTransfer.dropEffect = 'copy'
    }
  }, [])

  const onPageDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragOver(false)
      const file = e.dataTransfer.files?.[0]
      if (file) handleUpload(file)
    },
    [handleUpload],
  )

  const onFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) handleUpload(file)
    if (e.target) e.target.value = ''
  }, [handleUpload])

  // ── 进入编辑模式 ──
  function startEditProfile() {
    if (!data) return
    setEditProfile({ ...data.profile })
    setEditingProfile(true)
  }

  function startEditSkills() {
    if (!data) return
    setEditSkills(data.skills.map((s) => ({ name: s.name, level: s.level, context: s.context || '' })))
    setEditingSkills(true)
  }

  function startEditExperiences() {
    if (!data) return
    setEditExperiences(
      data.experiences.map((e) => ({
        title: e.title,
        description: e.description,
        period: e.period || '',
        tech_stack: e.tech_stack || '',
        role: e.role || '',
      })),
    )
    setEditingExperiences(true)
  }

  // ═══════════════════════════════════════════
  //  渲染区块
  // ═══════════════════════════════════════════

  function renderAboutYou() {
    if (!understanding?.about_you) {
      return (
        <Card title="关于你">
          <EmptyState
            message="AI 还没有形成对你的理解"
            hint="和 AI 多聊聊，它会逐渐了解你"
          />
        </Card>
      )
    }

    return (
      <Card
        title="关于你"
        action={
          <div className="flex gap-xs">
            <button
              onClick={handleRefreshAboutYou}
              disabled={busy}
              className="text-xs text-ink hover:text-ink-deep transition-colors px-sm py-1 rounded-md hover:bg-ink/10 disabled:opacity-50"
            >
              刷新
            </button>
            {editingAboutYou ? (
              <>
                <button
                  onClick={() => setEditingAboutYou(false)}
                  className="text-xs text-text-subtle hover:text-text transition-colors px-sm py-1 rounded-md hover:bg-ink/5"
                >
                  取消
                </button>
                <button
                  onClick={handleSaveCorrection}
                  disabled={busy}
                  className="text-xs bg-ink text-bg px-sm py-1 rounded-md hover:bg-ink-deep disabled:opacity-50 transition-colors"
                >
                  {busy ? '保存中…' : '保存'}
                </button>
              </>
            ) : (
              <button
                onClick={handleStartCorrection}
                className="text-xs text-ink hover:text-ink-deep transition-colors px-sm py-1 rounded-md hover:bg-ink/10"
              >
                纠正
              </button>
            )}
          </div>
        }
      >
        {editingAboutYou ? (
          <textarea
            value={editAboutYouText}
            onChange={(e) => setEditAboutYouText(e.target.value)}
            rows={8}
            className="w-full bg-bg border border-border-soft rounded-md px-md py-sm text-sm text-text placeholder:text-text-subtle focus:border-ink transition-colors resize-none"
          />
        ) : (
          <div className="space-y-sm">
            {understanding.about_you.split('\n\n').map((para, i) => (
              <p key={i} className="text-sm text-text leading-relaxed">
                {para}
              </p>
            ))}
            {understanding.updated_at && (
              <p className="text-xs text-text-subtle">
                更新于 {formatRelativeTime(understanding.updated_at)}
              </p>
            )}
          </div>
        )}
      </Card>
    )
  }

  function renderPatterns() {
    if (!understanding?.patterns?.length) return null
    return (
      <Card title="AI 注意到的">
        <div className="space-y-sm">
          {understanding.patterns.map((p, i) => (
            <div
              key={i}
              className="flex items-start gap-sm p-sm bg-bg rounded-lg border border-border-soft"
            >
              <span className="text-base">{_categoryIcon(p.category)}</span>
              <p className="text-sm text-text leading-relaxed">{p.insight}</p>
            </div>
          ))}
        </div>
      </Card>
    )
  }

  function renderNow() {
    const status = understanding?.now_status
    if (!status || Object.keys(status).length === 0) return null
    return (
      <Card title="此刻">
        <div className="space-y-xs">
          {Object.entries(status).map(([k, v]) => (
            <div key={k} className="flex items-baseline gap-xs">
              <span className="text-xs text-text-subtle shrink-0">{k}</span>
              <span className="text-sm text-text">{v}</span>
            </div>
          ))}
        </div>
      </Card>
    )
  }

  function renderJourney() {
    if (!understanding?.journey?.length) return null
    return (
      <Card title="你走过的路">
        <div className="relative pl-lg">
          {/* 竖线 */}
          <div className="absolute left-sm top-0 bottom-0 w-px bg-border-soft" />
          <div className="space-y-md">
            {understanding.journey.map((item) => (
              <div key={item.id} className="relative">
                {/* 圆点 */}
                <div className="absolute -left-lg top-1 w-2 h-2 rounded-full bg-ink/50 border border-ink" />
                <div>
                  <p className="text-sm text-text">{item.content}</p>
                  <p className="text-xs text-text-subtle mt-0.5">
                    {item.date ? formatDate(item.date) : ''}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </Card>
    )
  }

  // ═══════════════════════════════════════════
  //  主渲染
  // ═══════════════════════════════════════════

  if (loading) {
    return (
      <div className="mx-auto max-w-[900px] px-md py-2xl h-full">
        <div className="ink-progress mt-2xl" />
      </div>
    )
  }

  const profile = data?.profile || {}
  const skills = data?.skills || []
  const experiences = data?.experiences || []
  const goals = data?.goals || {}
  const preferences = data?.preferences || {}
  const status = data?.status || {}

  return (
    <div
      className={`relative mx-auto max-w-[900px] px-md py-xl h-full overflow-y-auto scroll-auto-hide ${
        dragOver ? 'ring-2 ring-primary/35 ring-inset rounded-xl' : ''
      }`}
      onDragEnter={onPageDragEnter}
      onDragLeave={onPageDragLeave}
      onDragOver={onPageDragOver}
      onDrop={onPageDrop}
    >
      {dragOver && !uploading && (
        <div className="pointer-events-none absolute inset-0 z-[1] rounded-xl bg-bg/75 border border-dashed border-primary/45 flex flex-col items-center justify-center gap-1">
          <p className="text-sm text-text">松开以上传简历</p>
          <p className="text-xs text-text-subtle">PDF、Word、TXT、Markdown</p>
        </div>
      )}

      {/* 页面头部 */}
      <header className="mb-xl relative z-0">
        <div className="flex items-start justify-between gap-md">
          <div className="min-w-0 flex-1">
            <h1 className="text-2xl font-han text-ink">AI 眼中的你</h1>
            <p className="text-text-muted text-sm mt-xs">
              这是 AI 从你们的对话中拼凑出的画像。你可以随时纠正它。
            </p>
          </div>
          <div className="flex items-center gap-sm shrink-0">
            <button
              onClick={() => fileInputRef.current?.click()}
              className="text-sm text-ink hover:text-ink-deep px-sm py-1 rounded-md hover:bg-ink/10 transition-colors"
            >
              上传简历
            </button>
            <button
              onClick={handleReset}
              disabled={busy}
              className="text-sm text-text-subtle hover:text-danger px-sm py-1 rounded-md hover:bg-danger/10 disabled:opacity-50 transition-colors"
            >
              {busy ? '处理中…' : '重置'}
            </button>
          </div>
        </div>

        {error && (
          <p role="alert" className="mt-md text-sm text-danger">
            {error}
          </p>
        )}

        {uploading && (
          <p className="mt-sm text-sm text-text-muted" aria-live="polite">
            解析简历中…
          </p>
        )}

        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.doc,.docx,.txt,.md"
          className="hidden"
          onChange={onFileChange}
        />
      </header>

      {/* 关于你 */}
      <section className="mb-lg">{renderAboutYou()}</section>

      {/* AI 注意到的 */}
      {renderPatterns() && <section className="mb-lg">{renderPatterns()}</section>}

      {/* 此刻 */}
      {renderNow() && <section className="mb-lg">{renderNow()}</section>}

      {/* 你走过的路 */}
      {renderJourney() && <section className="mb-lg">{renderJourney()}</section>}

      {/* 简历数据（折叠） */}
      <section className="mb-lg">
        <div className="mb-lg">
          <button
            onClick={() => setShowResumeData(!showResumeData)}
            className="w-full flex items-center justify-between px-md py-sm border border-border-soft rounded-xl bg-surface hover:bg-surface-elevated transition-colors"
          >
            <span className="text-sm font-medium text-text">简历数据</span>
            <span className="text-xs text-text-subtle">{showResumeData ? '收起' : '展开'}</span>
          </button>
          {showResumeData && (
            <div className="mt-sm space-y-lg">
              {/* 基础信息卡片 */}
              <Card
                title="基础信息"
                action={
                  editingProfile ? (
                    <div className="flex gap-xs">
                      <EditButton onClick={() => setEditingProfile(false)} editing />
                      <SaveButton onClick={saveProfile} loading={busy} />
                    </div>
                  ) : (
                    <EditButton onClick={startEditProfile} />
                  )
                }
              >
                {editingProfile ? (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-md">
                    {PROFILE_FIELDS.map((field) => (
                      <div key={field.key}>
                        <label className="block text-xs text-text-subtle mb-1">{field.label}</label>
                        {field.type === 'textarea' ? (
                          <textarea
                            value={String(editProfile[field.key] || '')}
                            onChange={(e) =>
                              setEditProfile((prev) => ({ ...prev, [field.key]: e.target.value }))
                            }
                            placeholder={field.placeholder}
                            rows={3}
                            className="w-full bg-bg border border-border-soft rounded-md px-sm py-1 text-sm text-text placeholder:text-text-subtle focus:border-ink transition-colors resize-none"
                          />
                        ) : field.type === 'select' ? (
                          <select
                            value={String(editProfile[field.key] || '')}
                            onChange={(e) =>
                              setEditProfile((prev) => ({ ...prev, [field.key]: e.target.value }))
                            }
                            className="w-full bg-bg border border-border-soft rounded-md px-sm py-1 text-sm text-text focus:border-ink transition-colors appearance-none cursor-pointer"
                          >
                            <option value="">{field.placeholder}</option>
                            {field.options?.map((opt) => (
                              <option key={opt} value={opt}>
                                {opt}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <input
                            type="text"
                            value={String(editProfile[field.key] || '')}
                            onChange={(e) =>
                              setEditProfile((prev) => ({ ...prev, [field.key]: e.target.value }))
                            }
                            placeholder={field.placeholder}
                            className="w-full bg-bg border border-border-soft rounded-md px-sm py-1 text-sm text-text placeholder:text-text-subtle focus:border-ink transition-colors"
                          />
                        )}
                      </div>
                    ))}
                  </div>
                ) : isEmptyProfile(profile) ? (
                  <EmptyState
                    message="还没有基础信息"
                    hint="在对话中告诉 AI，或点击右上角上传简历"
                  />
                ) : (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-md gap-y-sm">
                    {PROFILE_FIELDS.filter((f) => getProfileField(profile, f.key)).map((field) => (
                      <div key={field.key} className="flex items-baseline gap-xs">
                        <span className="text-xs text-text-subtle shrink-0">{field.label}</span>
                        <span className="text-sm text-text">{getProfileField(profile, field.key)}</span>
                      </div>
                    ))}
                    {profile.awards && Array.isArray(profile.awards) && profile.awards.length > 0 && (
                      <div className="col-span-full">
                        <span className="text-xs text-text-subtle">获奖</span>
                        <div className="flex flex-wrap gap-xs mt-1">
                          {(profile.awards as string[]).map((award) => (
                            <span
                              key={award}
                              className="text-xs bg-surface-elevated text-text-muted px-2 py-0.5 rounded-md border border-border-soft"
                            >
                              {award}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </Card>

              {/* 技能卡片 */}
              <Card
                title="技能"
                action={
                  editingSkills ? (
                    <div className="flex gap-xs">
                      <EditButton onClick={() => setEditingSkills(false)} editing />
                      <SaveButton onClick={saveSkills} loading={busy} />
                    </div>
                  ) : (
                    <EditButton onClick={startEditSkills} />
                  )
                }
              >
                {editingSkills ? (
                  <div className="space-y-sm">
                    {editSkills.map((skill, idx) => (
                      <div
                        key={idx}
                        className="flex items-center gap-sm p-sm bg-bg rounded-lg border border-border-soft"
                      >
                        <input
                          type="text"
                          value={skill.name}
                          onChange={(e) => {
                            const next = [...editSkills]
                            next[idx].name = e.target.value
                            setEditSkills(next)
                          }}
                          placeholder="技能名称"
                          className="flex-1 min-w-0 bg-transparent text-sm text-text placeholder:text-text-subtle focus:outline-none"
                        />
                        <select
                          value={skill.level}
                          onChange={(e) => {
                            const next = [...editSkills]
                            next[idx].level = e.target.value
                            setEditSkills(next)
                          }}
                          className="bg-surface-elevated text-sm text-text rounded-md px-2 py-1 border border-border-soft focus:border-ink cursor-pointer"
                        >
                          {SKILL_LEVELS.map((l) => (
                            <option key={l} value={l}>
                              {LEVEL_LABELS[l]}
                            </option>
                          ))}
                        </select>
                        <input
                          type="text"
                          value={skill.context}
                          onChange={(e) => {
                            const next = [...editSkills]
                            next[idx].context = e.target.value
                            setEditSkills(next)
                          }}
                          placeholder="备注（可选）"
                          className="w-32 bg-transparent text-xs text-text-muted placeholder:text-text-subtle focus:outline-none hidden sm:block"
                        />
                        <button
                          onClick={() => setEditSkills((prev) => prev.filter((_, i) => i !== idx))}
                          className="text-text-subtle hover:text-danger px-1 transition-colors"
                          title="删除"
                        >
                          ✕
                        </button>
                      </div>
                    ))}
                    <button
                      onClick={() =>
                        setEditSkills((prev) => [...prev, { name: '', level: 'familiar', context: '' }])
                      }
                      className="w-full py-sm text-sm text-ink hover:text-ink-deep border border-dashed border-border-soft rounded-lg hover:border-ink/50 transition-colors"
                    >
                      + 添加技能
                    </button>
                  </div>
                ) : skills.length === 0 ? (
                  <EmptyState message="还没有记录技能" hint="告诉 AI 你擅长什么，或点击编辑手动添加" />
                ) : (
                  <div className="flex flex-wrap gap-sm">
                    {skills.map((skill) => (
                      <div
                        key={skill.name}
                        className={`group flex items-center gap-xs px-3 py-1.5 rounded-full border text-sm transition-all hover:scale-105 ${
                          LEVEL_COLORS[skill.level] || LEVEL_COLORS.familiar
                        } bg-surface-elevated`}
                        title={skill.context || ''}
                      >
                        <span className="font-medium">{skill.name}</span>
                        <span className="text-xs opacity-70">{LEVEL_LABELS[skill.level] || skill.level}</span>
                      </div>
                    ))}
                  </div>
                )}
              </Card>

              {/* 经历卡片 */}
              <Card
                title="经历"
                action={
                  editingExperiences ? (
                    <div className="flex gap-xs">
                      <EditButton onClick={() => setEditingExperiences(false)} editing />
                      <SaveButton onClick={saveExperiences} loading={busy} />
                    </div>
                  ) : (
                    <EditButton onClick={startEditExperiences} />
                  )
                }
              >
                {editingExperiences ? (
                  <div className="space-y-md">
                    {editExperiences.map((exp, idx) => (
                      <div key={idx} className="p-md bg-bg rounded-lg border border-border-soft space-y-sm">
                        <div className="flex items-center gap-sm">
                          <input
                            type="text"
                            value={exp.title}
                            onChange={(e) => {
                              const next = [...editExperiences]
                              next[idx].title = e.target.value
                              setEditExperiences(next)
                            }}
                            placeholder="经历名称（项目/实习/竞赛）"
                            className="flex-1 bg-transparent text-sm text-text font-medium placeholder:text-text-subtle focus:outline-none"
                          />
                          <button
                            onClick={() => setEditExperiences((prev) => prev.filter((_, i) => i !== idx))}
                            className="text-text-subtle hover:text-danger px-1 transition-colors"
                          >
                            ✕
                          </button>
                        </div>
                        <div className="grid grid-cols-2 gap-sm">
                          <input
                            type="text"
                            value={exp.period}
                            onChange={(e) => {
                              const next = [...editExperiences]
                              next[idx].period = e.target.value
                              setEditExperiences(next)
                            }}
                            placeholder="时间段"
                            className="bg-surface-elevated rounded-md px-sm py-1 text-xs text-text placeholder:text-text-subtle border border-border-soft focus:border-ink"
                          />
                          <input
                            type="text"
                            value={exp.role}
                            onChange={(e) => {
                              const next = [...editExperiences]
                              next[idx].role = e.target.value
                              setEditExperiences(next)
                            }}
                            placeholder="角色"
                            className="bg-surface-elevated rounded-md px-sm py-1 text-xs text-text placeholder:text-text-subtle border border-border-soft focus:border-ink"
                          />
                        </div>
                        <input
                          type="text"
                          value={exp.tech_stack}
                          onChange={(e) => {
                            const next = [...editExperiences]
                            next[idx].tech_stack = e.target.value
                            setEditExperiences(next)
                          }}
                          placeholder="技术栈（可选）"
                          className="w-full bg-surface-elevated rounded-md px-sm py-1 text-xs text-text placeholder:text-text-subtle border border-border-soft focus:border-ink"
                        />
                        <textarea
                          value={exp.description}
                          onChange={(e) => {
                            const next = [...editExperiences]
                            next[idx].description = e.target.value
                            setEditExperiences(next)
                          }}
                          placeholder="描述"
                          rows={2}
                          className="w-full bg-surface-elevated rounded-md px-sm py-1 text-xs text-text placeholder:text-text-subtle border border-border-soft focus:border-ink resize-none"
                        />
                      </div>
                    ))}
                    <button
                      onClick={() =>
                        setEditExperiences((prev) => [
                          ...prev,
                          { title: '', description: '', period: '', tech_stack: '', role: '' },
                        ])
                      }
                      className="w-full py-sm text-sm text-ink hover:text-ink-deep border border-dashed border-border-soft rounded-lg hover:border-ink/50 transition-colors"
                    >
                      + 添加经历
                    </button>
                  </div>
                ) : experiences.length === 0 ? (
                  <EmptyState message="还没有记录经历" hint="告诉 AI 你的项目或实习经历" />
                ) : (
                  <div className="space-y-md">
                    {experiences.map((exp) => (
                      <div
                        key={exp.title}
                        className="p-md bg-bg rounded-lg border border-border-soft hover:border-border transition-colors"
                      >
                        <div className="flex items-start justify-between">
                          <h3 className="text-sm font-medium text-text">{exp.title}</h3>
                          {exp.period && <span className="text-xs text-text-subtle shrink-0">{exp.period}</span>}
                        </div>
                        {exp.role && <p className="text-xs text-text-muted mt-0.5">{exp.role}</p>}
                        {exp.tech_stack && (
                          <div className="flex flex-wrap gap-xs mt-sm">
                            {exp.tech_stack
                              .split(/[,，]/)
                              .map((t) => t.trim())
                              .filter(Boolean)
                              .map((tech) => (
                                <span
                                  key={tech}
                                  className="text-xs bg-surface-elevated text-text-muted px-2 py-0.5 rounded-md border border-border-soft"
                                >
                                  {tech}
                                </span>
                              ))}
                          </div>
                        )}
                        {exp.description && (
                          <p className="text-sm text-text-muted mt-sm leading-relaxed">{exp.description}</p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </Card>

              {/* 偏好 & 目标 & 状态 */}
              {(Object.keys(preferences).length > 0 || Object.keys(goals).length > 0 || Object.keys(status).length > 0) && (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-md">
                  {Object.keys(goals).length > 0 && (
                    <Card title="目标">
                      <ul className="space-y-xs">
                        {Object.entries(goals).map(([k, v]) => (
                          <li key={k} className="text-sm">
                            <span className="text-text-subtle text-xs">{k}</span>
                            <p className="text-text">{v}</p>
                          </li>
                        ))}
                      </ul>
                    </Card>
                  )}
                  {Object.keys(preferences).length > 0 && (
                    <Card title="偏好">
                      <ul className="space-y-xs">
                        {Object.entries(preferences).map(([k, v]) => (
                          <li key={k} className="text-sm">
                            <span className="text-text-subtle text-xs">{k}</span>
                            <p className="text-text">{v}</p>
                          </li>
                        ))}
                      </ul>
                    </Card>
                  )}
                  {Object.keys(status).length > 0 && (
                    <Card title="当前状态">
                      <ul className="space-y-xs">
                        {Object.entries(status).map(([k, v]) => (
                          <li key={k} className="text-sm">
                            <span className="text-text-subtle text-xs">{k}</span>
                            <p className="text-text">{v}</p>
                          </li>
                        ))}
                      </ul>
                    </Card>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </section>

      {/* 底部提示 */}
      <footer className="text-center py-xl">
        <p className="text-text-subtle text-xs">
          这些信息由 AI 从对话中自动提取，你也可以随时编辑纠正
        </p>
      </footer>
    </div>
  )
}
