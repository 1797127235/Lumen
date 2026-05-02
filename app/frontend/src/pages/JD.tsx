import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  diagnoseJD,
  getProfile,
  getJDHistory,
  deleteJDDiagnosis,
  type JDHistoryItem,
  type Profile,
} from '../lib/api'

function isFilled(p: Profile | null): boolean {
  if (!p) return false
  return Boolean(
    p.school_name ||
      p.major ||
      p.target_direction ||
      (p.current_skills && p.current_skills.length > 0),
  )
}

export default function JDPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [profileFilled, setProfileFilled] = useState<boolean | null>(null)
  const [history, setHistory] = useState<JDHistoryItem[]>([])

  // 从 JDReport 页"重新诊断"带回来的 jd_text
  useEffect(() => {
    const jdText = (location.state as { jdText?: string })?.jdText
    if (jdText) {
      setText(jdText)
      // 清空 state，避免刷新时重复填充
      window.history.replaceState({}, '')
    }
  }, [location.state])

  useEffect(() => {
    getProfile()
      .then((p) => setProfileFilled(isFilled(p)))
      .catch(() => setProfileFilled(false))
    loadHistory()
  }, [])

  async function loadHistory() {
    try {
      const res = await getJDHistory()
      setHistory(res.items)
    } catch {
      // 静默失败
    }
  }

  async function submit() {
    const trimmed = text.trim()
    if (trimmed.length < 10) {
      setError('再多贴点,这样我看不出来.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const res = await diagnoseJD(trimmed)
      if (res.diagnosis_id) {
        navigate(`/jd/${res.diagnosis_id}`)
      }
    } catch (e) {
      setError((e as Error).message || '我刚才走神了,你再问我一遍.')
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete(diagnosisId: string) {
    try {
      await deleteJDDiagnosis(diagnosisId)
      setHistory((prev) => prev.filter((h) => h.diagnosis_id !== diagnosisId))
    } catch (e) {
      setError((e as Error).message || '删除失败')
    }
  }

  return (
    <div className="mx-auto max-w-[820px] px-md py-2xl flex flex-col gap-2xl">
      <header className="ink-fade-in">
        <h2 className="text-2xl">把你看上的 JD 贴进来,</h2>
        <h2 className="text-2xl">我帮你看看够不够格.</h2>
      </header>

      {profileFilled === false ? (
        <p className="text-text-muted text-sm ink-fade-in">
          你还没填画像, 我只能笼统说说.{' '}
          <Link to="/profile" className="text-ink hover:text-ink-deep">
            先去画像页?
          </Link>
        </p>
      ) : null}

      <div className="flex flex-col gap-md">
        <div className="bg-surface rounded-md p-md border-y border-border-soft">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="把你看上的那段贴上来,我们对一对."
            rows={10}
            className="w-full min-h-[240px] resize-y bg-transparent py-md text-base leading-relaxed placeholder:text-text-subtle"
          />
        </div>
        <div className="flex justify-center">
          <button
            onClick={submit}
            disabled={busy || text.trim().length === 0}
            className="text-ink hover:text-ink-deep text-lg disabled:text-text-subtle disabled:cursor-not-allowed"
            aria-label="提交 JD 让学长看看"
          >
            {busy ? '…' : '让学长看看'}
          </button>
        </div>
      </div>

      {busy ? (
        <div className="flex flex-col gap-xs">
          <p className="text-text-muted text-sm">我在比对...</p>
          <div className="ink-progress" />
        </div>
      ) : null}

      {error ? <p className="text-danger text-sm">{error}</p> : null}

      {/* 历史列表 */}
      {history.length > 0 ? (
        <div className="flex flex-col gap-md">
          <div className="h-px w-full bg-border-soft" />
          <h3 className="text-lg">诊断历史</h3>
          <ul className="flex flex-col gap-xs">
            {history.map((item) => (
              <HistoryItem
                key={item.diagnosis_id}
                item={item}
                onDelete={() => handleDelete(item.diagnosis_id)}
              />
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

function HistoryItem({
  item,
  onDelete,
}: {
  item: JDHistoryItem
  onDelete: () => void
}) {
  const [confirming, setConfirming] = useState(false)

  useEffect(() => {
    if (!confirming) return
    const t = window.setTimeout(() => setConfirming(false), 3000)
    return () => window.clearTimeout(t)
  }, [confirming])

  const date = new Date(item.created_at)
  const now = new Date()
  const dateStr =
    date.getFullYear() === now.getFullYear()
      ? `${date.getMonth() + 1}-${date.getDate()}`
      : `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`

  function handleClick() {
    if (!confirming) {
      setConfirming(true)
      return
    }
    onDelete()
  }

  return (
    <li className="group flex items-center gap-md">
      <Link
        to={`/jd/${item.diagnosis_id}`}
        className="flex-1 flex items-center gap-md text-base text-text hover:text-ink"
      >
        <span className="flex-1 truncate">{item.jd_title}</span>
        <span className="text-text-muted text-sm">{item.overall_score}分</span>
        <span className="text-text-subtle text-sm">{dateStr}</span>
      </Link>
      <button
        onClick={handleClick}
        className={[
          'text-xs whitespace-nowrap transition-colors',
          confirming ? 'text-danger' : 'text-text-subtle opacity-0 group-hover:opacity-100 hover:text-danger',
        ].join(' ')}
      >
        {confirming ? '确定删除?' : '✕'}
      </button>
    </li>
  )
}
