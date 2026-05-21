import { useEffect, useState, useRef } from 'react'
import { getCurrentMood, type MoodState } from '../lib/api/companion'

// 情绪配置
const MOOD_CONFIG: Record<
  MoodState['mood'],
  { label: string; icon: string; color: string; animation: string }
> = {
  calm:        { label: '平静',  icon: '🌿', color: 'text-emerald-500', animation: 'animate-breathe' },
  curious:     { label: '好奇',  icon: '✨', color: 'text-amber-500',   animation: 'animate-pulse-gentle' },
  tender:      { label: '温柔',  icon: '💙', color: 'text-blue-400',    animation: 'animate-heartbeat' },
  reflective:  { label: '沉思',  icon: '🤔', color: 'text-violet-400',  animation: 'animate-orbit' },
  energized:   { label: '活跃',  icon: '🌤', color: 'text-orange-400',  animation: 'animate-bounce-soft' },
}

type StripState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: MoodState; transitioning: boolean }
  | { kind: 'error' }

export default function MoodStrip() {
  const [state, setState] = useState<StripState>({ kind: 'loading' })
  const prevMood = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false

    getCurrentMood()
      .then((data) => {
        if (cancelled) return
        const isTransitioning = prevMood.current !== null && prevMood.current !== data.mood
        setState({ kind: 'ready', data, transitioning: isTransitioning })
        if (isTransitioning) {
          // 600ms 过渡后关闭过渡标志
          setTimeout(() => {
            if (!cancelled) {
              setState(s => s.kind === 'ready' ? { ...s, transitioning: false } : s)
            }
          }, 600)
        }
        prevMood.current = data.mood
      })
      .catch(() => {
        if (cancelled) return
        setState({ kind: 'error' })
      })

    // 每 5 分钟轮询一次
    const interval = setInterval(() => {
      getCurrentMood()
        .then((data) => {
          if (cancelled) return
          const isTransitioning = prevMood.current !== null && prevMood.current !== data.mood
          setState({ kind: 'ready', data, transitioning: isTransitioning })
          if (isTransitioning) {
            setTimeout(() => {
              if (!cancelled) {
                setState(s => s.kind === 'ready' ? { ...s, transitioning: false } : s)
              }
            }, 600)
          }
          prevMood.current = data.mood
        })
        .catch(() => { /* 轮询失败静默处理，保留上次状态 */ })
    }, 5 * 60 * 1000)

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  // 加载中或出错时兜底显示 calm，保证常驻可见
  const isLoading = state.kind === 'loading'
  const isError = state.kind === 'error'
  const moodData: MoodState =
    state.kind === 'ready'
      ? state.data
      : { mood: 'calm', mood_intensity: 0.4, updated_at: null }
  const transitioning = state.kind === 'ready' && state.transitioning

  const cfg = MOOD_CONFIG[moodData.mood]
  const transitionClass = transitioning
    ? 'transition-all duration-500 opacity-0 scale-95'
    : 'transition-all duration-500 opacity-100 scale-100'

  return (
    <div className={`flex items-center justify-between px-md py-xs ${transitionClass}`}>
      <div className="flex items-center gap-xs text-xs text-text-subtle">
        <span
          className={`${cfg.color} ${cfg.animation} select-none`}
          title={`Lumen 正在·${cfg.label}中`}
          style={{ fontSize: '14px' }}
        >
          {cfg.icon}
        </span>
        <span>Lumen 正在·{cfg.label}中</span>
        {(isLoading || isError) && (
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-text-subtle/30 animate-pulse ml-1" />
        )}
      </div>
      <a
        href="/inner-world"
        className="text-xs text-text-subtle hover:text-text-muted transition-colors"
      >
        查看 Lumen 的内心 →
      </a>
    </div>
  )
}
