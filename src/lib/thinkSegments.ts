/**
 * 解析 <think>...</think> / <thinking>...</thinking> 标签，
 * 将原始 assistant 响应拆分为文本段和思考段。
 *
 * 流式兼容：开放标签未关闭时，closed=false。
 */

export interface TextSegment {
  kind: 'text'
  content: string
}

export interface ThinkSegment {
  kind: 'think'
  content: string
  closed: boolean
}

export type ContentSegment = TextSegment | ThinkSegment

const OPEN_RE = /<think(?:ing)?\b[^>]*>/i
const CLOSE_RE = /<\/think(?:ing)?>/i

function trim(s: string): string {
  return s.trim()
}

export function parseThinkSegments(input: string): ContentSegment[] {
  if (!input) return []
  if (!OPEN_RE.test(input)) return [{ kind: 'text', content: input }]

  const segments: ContentSegment[] = []
  let cursor = 0

  while (cursor < input.length) {
    const tail = input.slice(cursor)
    const open = OPEN_RE.exec(tail)
    if (!open) {
      if (tail) segments.push({ kind: 'text', content: tail })
      break
    }
    if (open.index > 0) {
      segments.push({ kind: 'text', content: tail.slice(0, open.index) })
    }
    const afterOpen = tail.slice(open.index + open[0].length)
    const close = CLOSE_RE.exec(afterOpen)
    if (!close) {
      segments.push({ kind: 'think', content: trim(afterOpen), closed: false })
      break
    }
    segments.push({
      kind: 'think',
      content: trim(afterOpen.slice(0, close.index)),
      closed: true,
    })
    cursor += open.index + open[0].length + close.index + close[0].length
  }

  return segments
}
