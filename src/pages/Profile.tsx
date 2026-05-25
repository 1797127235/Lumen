import { useCallback, useEffect, useState } from 'react'
import {
  getAIUnderstanding,
  resetMemory,
  refreshAIUnderstanding,
  correctAIUnderstanding,
  tellAI,
  type AboutYouResponse,
  type TellType,
} from '../lib/api'
import { Card } from '../components/Card'
import { EmptyState } from '../components/EmptyState'

// ═══════════════════════════════════════════
//  工具函数
// ═══════════════════════════════════════════

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

// ═══════════════════════════════════════════
//  主页面
// ═══════════════════════════════════════════

export default function ProfilePage() {
  const [understanding, setUnderstanding] = useState<AboutYouResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [editingAboutYou, setEditingAboutYou] = useState(false)
  const [editAboutYouText, setEditAboutYouText] = useState('')

  // 告诉 AI
  const [tellType, setTellType] = useState<TellType>('interest')
  const [tellContent, setTellContent] = useState('')
  const [tellSuccess, setTellSuccess] = useState<string | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getAIUnderstanding()
      setUnderstanding(data)
    } catch (e) {
      setError((e as Error).message || '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  async function handleReset() {
    if (busy) return
    if (!confirm('确定要重置所有记忆吗？这会清空 AI 对你的全部理解。')) return
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

  async function handleTellAI() {
    if (!tellContent.trim()) return
    setBusy(true)
    setTellSuccess(null)
    try {
      await tellAI(tellType, tellContent.trim())
      setTellSuccess('已记录')
      setTellContent('')
      await loadData()
    } catch (e) {
      setError((e as Error).message || '记录失败')
    } finally {
      setBusy(false)
    }
  }

  // ── 渲染区块 ──

  function renderAboutYou() {
    if (!understanding?.about_you) {
      return (
        <Card title="关于你">
          <EmptyState
            message="AI 还没有形成对你的理解"
            hint="多和 Lumen 聊聊，它会逐渐认识你"
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

  function renderTellAI() {
    const typeLabels: Record<TellType, string> = {
      interest: '兴趣',
      value: '价值观',
      relationship: '重要的人',
      moment: '重要经历',
      reflection: '想法',
    }
    return (
      <Card title="告诉 Lumen">
        <div className="space-y-sm">
          <p className="text-xs text-text-subtle">
            主动告诉 Lumen 关于你的事，它会记得更牢
          </p>
          <div className="flex gap-xs">
            {(Object.keys(typeLabels) as TellType[]).map((t) => (
              <button
                key={t}
                onClick={() => setTellType(t)}
                className={`text-xs px-sm py-1 rounded-md transition-colors ${
                  tellType === t
                    ? 'bg-ink text-bg'
                    : 'bg-surface-elevated text-text-subtle hover:text-text'
                }`}
              >
                {typeLabels[t]}
              </button>
            ))}
          </div>
          <textarea
            value={tellContent}
            onChange={(e) => setTellContent(e.target.value)}
            placeholder={`分享一件关于你的${typeLabels[tellType]}...`}
            rows={3}
            className="w-full bg-bg border border-border-soft rounded-md px-md py-sm text-sm text-text placeholder:text-text-subtle focus:border-ink transition-colors resize-none"
          />
          <div className="flex items-center gap-sm">
            <button
              onClick={handleTellAI}
              disabled={busy || !tellContent.trim()}
              className="text-xs bg-ink text-bg px-sm py-1 rounded-md hover:bg-ink-deep disabled:opacity-50 transition-colors"
            >
              {busy ? '记录中…' : '告诉它'}
            </button>
            {tellSuccess && (
              <span className="text-xs text-success">{tellSuccess}</span>
            )}
          </div>
        </div>
      </Card>
    )
  }

  // ── 主渲染 ──

  if (loading) {
    return (
      <div className="mx-auto max-w-[900px] px-md py-2xl h-full">
        <div className="ink-progress mt-2xl" />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[900px] px-md py-xl h-full overflow-y-auto scroll-auto-hide">
      <header className="mb-xl">
        <div className="flex items-start justify-between gap-md">
          <div className="min-w-0 flex-1">
            <h1 className="text-2xl font-han text-ink">AI 眼中的你</h1>
            <p className="text-text-muted text-sm mt-xs">
              这是 Lumen 从你们的对话中拼凑出的画像。你可以随时纠正它。
            </p>
          </div>
          <button
            onClick={handleReset}
            disabled={busy}
            className="text-sm text-text-subtle hover:text-danger px-sm py-1 rounded-md hover:bg-danger/10 disabled:opacity-50 transition-colors shrink-0"
          >
            {busy ? '处理中…' : '重置'}
          </button>
        </div>

        {error && (
          <p role="alert" className="mt-md text-sm text-danger">
            {error}
          </p>
        )}
      </header>

      <section className="mb-lg">{renderAboutYou()}</section>

      <section className="mb-lg">{renderTellAI()}</section>

      <footer className="text-center py-xl">
        <p className="text-text-subtle text-xs">
          这些理解由 AI 从对话中自动生成，你也可以随时纠正
        </p>
      </footer>
    </div>
  )
}
