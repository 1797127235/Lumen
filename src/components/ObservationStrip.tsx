import { useEffect, useState } from 'react'
import { getObservations, type Observation } from '../lib/api/memory'

type State =
  | { kind: 'loading' }
  | { kind: 'empty' }
  | { kind: 'ready'; observations: Observation[] }
  | { kind: 'error' }

export default function ObservationStrip() {
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [collapsed, setCollapsed] = useState(false)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    let cancelled = false
    getObservations(7)
      .then((result) => {
        if (cancelled) return
        if (result.observations.length === 0) {
          setState({ kind: 'empty' })
        } else {
          setState({ kind: 'ready', observations: result.observations })
        }
      })
      .catch((err) => {
        if (cancelled) return
        console.warn('observations 获取失败', err)
        setState({ kind: 'error' })
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (state.kind === 'empty' || state.kind === 'error' || dismissed) {
    return null
  }

  if (state.kind === 'loading') {
    return (
      <div className="ink-fade-in mb-lg rounded-xl border border-border-soft bg-surface/40 px-md py-sm">
        <div className="flex items-center gap-xs text-xs text-text-subtle/70">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-text-subtle/40" />
          <span>Lumen 正在整理关于你的观察…</span>
        </div>
      </div>
    )
  }

  return (
    <div className="ink-fade-in mb-lg overflow-hidden rounded-xl border border-border-soft bg-surface/60">
      <div className="flex items-center justify-between px-md pt-sm pb-2xs">
        <div className="flex items-center gap-xs text-xs text-text-subtle">
          <svg
            className="h-3.5 w-3.5"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            strokeWidth={1.5}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456z"
            />
          </svg>
          <span>Lumen 注意到的</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setCollapsed((v) => !v)}
            className="rounded p-1 text-text-subtle/60 transition-colors hover:bg-surface-elevated hover:text-text-muted"
            aria-label={collapsed ? '展开观察' : '折叠观察'}
          >
            <svg
              className={`h-3 w-3 transition-transform ${collapsed ? '' : 'rotate-180'}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          <button
            type="button"
            onClick={() => setDismissed(true)}
            className="rounded p-1 text-text-subtle/60 transition-colors hover:bg-surface-elevated hover:text-text-muted"
            aria-label="关闭"
          >
            <svg
              className="h-3 w-3"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      {!collapsed && (
        <ul className="flex flex-col gap-xs px-md pb-sm">
          {state.observations.map((obs, i) => (
            <li
              key={i}
              className="flex gap-xs text-sm leading-relaxed text-text"
            >
              <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-text-subtle/60" />
              <span>{obs.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
