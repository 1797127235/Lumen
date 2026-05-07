/**
 * WebSocket 客户端 — 心跳 + 自动重连 + 取消支持
 */

export type WSHandlers = {
  onToken: (delta: string, conversationId: string) => void
  onDone: (conversationId: string, usage?: { input: number; output: number }) => void
  onCancelled: () => void
  onError: (message: string) => void
}

type WSState = 'connecting' | 'connected' | 'disconnected'

const PING_INTERVAL = 30_000 // 30s
const RECONNECT_DELAY = 2_000 // 2s
const MAX_RECONNECT = 5

export class ChatWS {
  private ws: WebSocket | null = null
  private handlers: WSHandlers
  private state: WSState = 'disconnected'
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private reconnectCount = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private url: string

  constructor(handlers: WSHandlers) {
    this.handlers = handlers
    // 自动检测 WebSocket URL
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    this.url = `${proto}//${window.location.host}/api/chat/ws`
  }

  connect() {
    if (this.state === 'connected' || this.state === 'connecting') return
    this.state = 'connecting'

    try {
      this.ws = new WebSocket(this.url)

      this.ws.onopen = () => {
        this.state = 'connected'
        this.reconnectCount = 0
        this.startPing()
      }

      this.ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)
          this.handleMessage(msg)
        } catch {
          // ignore malformed messages
        }
      }

      this.ws.onclose = (e) => {
        this.state = 'disconnected'
        this.stopPing()
        // 只在非正常关闭时重连
        if (e.code !== 1000) {
          this.tryReconnect()
        }
      }

      this.ws.onerror = () => {
        // onclose will fire after onerror
      }
    } catch {
      this.state = 'disconnected'
      this.tryReconnect()
    }
  }

  disconnect() {
    this.stopPing()
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.reconnectCount = MAX_RECONNECT // prevent reconnect
    this.ws?.close()
    this.ws = null
    this.state = 'disconnected'
  }

  send(content: string, conversationId?: string, userId?: string) {
    if (this.state !== 'connected' || !this.ws) {
      this.handlers.onError('连接未就绪，请稍后重试')
      return
    }

    this.ws.send(
      JSON.stringify({
        type: 'chat',
        content,
        conversation_id: conversationId ?? undefined,
        user_id: userId ?? undefined,
      }),
    )
  }

  cancel() {
    if (this.state !== 'connected' || !this.ws) return
    this.ws.send(JSON.stringify({ type: 'cancel' }))
  }

  get isConnected() {
    return this.state === 'connected'
  }

  private handleMessage(msg: Record<string, unknown>) {
    switch (msg.type) {
      case 'token':
        this.handlers.onToken(
          String(msg.content ?? ''),
          String(msg.conversation_id ?? ''),
        )
        break
      case 'done':
        this.handlers.onDone(
          String(msg.conversation_id ?? ''),
          msg.usage as { input: number; output: number } | undefined,
        )
        break
      case 'cancelled':
        this.handlers.onCancelled()
        break
      case 'error':
        this.handlers.onError(String(msg.message ?? '未知错误'))
        break
      case 'pong':
        // heartbeat ack
        break
    }
  }

  private startPing() {
    this.stopPing()
    this.pingTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping' }))
      }
    }, PING_INTERVAL)
  }

  private stopPing() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer)
      this.pingTimer = null
    }
  }

  private tryReconnect() {
    if (this.reconnectCount >= MAX_RECONNECT) return
    this.reconnectCount++
    this.reconnectTimer = setTimeout(() => {
      this.connect()
    }, RECONNECT_DELAY * this.reconnectCount)
  }
}
