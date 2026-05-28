export const Locale = {
  truncate(s: string, max: number): string {
    if (!s) return s
    return s.length > max ? s.slice(0, max - 1) + "…" : s
  },
  truncateLeft(s: string, max: number): string {
    if (!s) return s
    return s.length > max ? "…" + s.slice(s.length - max + 1) : s
  },
  truncateMiddle(s: string, max: number): string {
    if (!s || s.length <= max) return s
    const half = Math.floor((max - 1) / 2)
    return s.slice(0, half) + "…" + s.slice(s.length - (max - half - 1))
  },
  titlecase(s: string): string {
    if (!s) return s
    return s.charAt(0).toUpperCase() + s.slice(1)
  },
  datetime(ms: number): string {
    return new Date(ms).toLocaleString()
  },
  time(ms: number): string {
    return new Date(ms).toLocaleTimeString()
  },
  duration(ms: number): string {
    const s = Math.floor(ms / 1000)
    if (s < 60) return `${s}s`
    const m = Math.floor(s / 60)
    if (m < 60) return `${m}m ${s % 60}s`
    const h = Math.floor(m / 60)
    return `${h}h ${m % 60}m`
  },
  number(n: number): string {
    return n.toLocaleString()
  },
}
