// Phone-side state: stored credentials, the camera socket with reconnect (F-PHONE-4/6/7).

import { backoffDelay } from './ws'

export interface PhoneCredentials {
  camera_id: string
  camera_name: string
  session_name: string
  device_token: string
  ws_url: string
  remember: boolean
}

const KEY = 'publicsafe.phone'

export function loadCredentials(): PhoneCredentials | null {
  for (const store of [window.sessionStorage, window.localStorage]) {
    try {
      const raw = store.getItem(KEY)
      if (raw) return JSON.parse(raw) as PhoneCredentials
    } catch {
      /* storage unavailable */
    }
  }
  return null
}

export function saveCredentials(creds: PhoneCredentials): void {
  const target = creds.remember ? window.localStorage : window.sessionStorage
  const other = creds.remember ? window.sessionStorage : window.localStorage
  try {
    target.setItem(KEY, JSON.stringify(creds))
    other.removeItem(KEY)
  } catch {
    /* storage unavailable */
  }
}

export function forgetCredentials(): void {
  try {
    window.sessionStorage.removeItem(KEY)
    window.localStorage.removeItem(KEY)
  } catch {
    /* storage unavailable */
  }
}

export interface CaptureSettings {
  target_fps: number
  jpeg_quality: number
  max_long_edge: number
  confidence: number
  privacy_mode: boolean
}

export type PhoneState = 'idle' | 'connecting' | 'streaming' | 'paused' | 'reconnecting' | 'stopped'

export interface PhoneStatus {
  state: PhoneState
  attempt: number
  reason: string | null
  stopCode: number | null
  settings: CaptureSettings | null
  cameraName: string | null
  lastAckAt: number | null
  lastAckSeq: number | null
  processedSeq: number | null
  rttMs: number | null
  droppedReplaced: number
  droppedRatelimit: number
}

type Listener = (status: PhoneStatus) => void

/** Opens /ws/camera, authenticates, keeps pinging, reconnects with backoff. */
export class PhoneSocket {
  private ws: WebSocket | null = null
  private timer: number | null = null
  private pingTimer: number | null = null
  private wanted = false
  private paused = false
  private listeners = new Set<Listener>()
  status: PhoneStatus = {
    state: 'idle',
    attempt: 0,
    reason: null,
    stopCode: null,
    settings: null,
    cameraName: null,
    lastAckAt: null,
    lastAckSeq: null,
    processedSeq: null,
    rttMs: null,
    droppedReplaced: 0,
    droppedRatelimit: 0,
  }

  private readonly creds: PhoneCredentials
  private readonly clientInfo: { ua: string; w: number; h: number; remember: boolean }

  constructor(
    creds: PhoneCredentials,
    clientInfo: { ua: string; w: number; h: number; remember: boolean },
  ) {
    this.creds = creds
    this.clientInfo = clientInfo
  }

  on(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private set(patch: Partial<PhoneStatus>): void {
    this.status = { ...this.status, ...patch }
    this.listeners.forEach((l) => l(this.status))
  }

  get open(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN
  }

  /** Bytes queued in the socket buffer (F-PHONE-3 back-pressure). */
  get bufferedAmount(): number {
    return this.ws?.bufferedAmount ?? 0
  }

  start(): void {
    if (this.wanted) return
    this.wanted = true
    this.connect()
  }

  stop(code: number | null = null, reason: string | null = null): void {
    this.wanted = false
    this.clearTimers()
    const ws = this.ws
    this.ws = null
    ws?.close(1000, 'client_stop')
    this.set({ state: 'stopped', stopCode: code, reason })
  }

  private clearTimers(): void {
    if (this.timer) window.clearTimeout(this.timer)
    if (this.pingTimer) window.clearInterval(this.pingTimer)
    this.timer = null
    this.pingTimer = null
  }

  private connect(): void {
    this.set({ state: this.status.attempt > 0 ? 'reconnecting' : 'connecting', reason: null })
    const ws = new WebSocket(this.creds.ws_url)
    ws.binaryType = 'arraybuffer'
    this.ws = ws
    ws.onopen = () => {
      ws.send(
        JSON.stringify({ type: 'auth', token: this.creds.device_token, client: this.clientInfo }),
      )
    }
    ws.onmessage = (event) => {
      if (typeof event.data !== 'string') return
      let msg: Record<string, unknown>
      try {
        msg = JSON.parse(event.data) as Record<string, unknown>
      } catch {
        return
      }
      this.handle(msg)
    }
    ws.onclose = (event) => {
      if (this.ws !== ws) return
      this.ws = null
      this.clearTimers()
      if (!this.wanted) return
      if (event.code === 4003) {
        forgetCredentials()
        this.stop(4003, event.reason || 'revoked')
        return
      }
      if (event.code === 4004) {
        this.stop(4004, 'superseded')
        return
      }
      if (event.code === 4008) {
        this.stop(4008, 'rate_abuse')
        return
      }
      const attempt = this.status.attempt + 1
      this.set({ state: 'reconnecting', attempt, reason: event.reason || `close ${event.code}` })
      this.timer = window.setTimeout(() => this.connect(), backoffDelay(attempt))
    }
  }

  private handle(msg: Record<string, unknown>): void {
    switch (msg.type) {
      case 'hello': {
        const settings = msg.settings as CaptureSettings
        this.set({
          state: this.paused ? 'paused' : 'streaming',
          attempt: 0,
          reason: null,
          settings,
          cameraName: (msg.camera_name as string) ?? this.creds.camera_name,
        })
        this.pingTimer = window.setInterval(() => this.ping(), 5000)
        this.ping()
        if (this.paused) this.send({ type: 'pause' })
        break
      }
      case 'settings':
        this.set({ settings: msg.settings as CaptureSettings })
        break
      case 'pong': {
        const sent = msg.t_phone_ms as number
        this.set({ rttMs: Math.max(0, Math.round(Date.now() - sent)) })
        break
      }
      case 'ack':
        this.set({
          lastAckAt: Date.now(),
          lastAckSeq: msg.seq as number,
          processedSeq: (msg.processed_seq as number | null) ?? null,
          droppedReplaced: (msg.dropped_replaced as number) ?? 0,
          droppedRatelimit: (msg.dropped_ratelimit as number) ?? 0,
        })
        break
      case 'error':
        this.set({ reason: `${msg.code}: ${msg.message}` })
        break
      default:
        break
    }
  }

  private ping(): void {
    if (!this.open) return
    this.send({ type: 'ping', t_phone_ms: Date.now(), last_rtt_ms: this.status.rttMs })
  }

  send(message: Record<string, unknown>): void {
    if (this.open) this.ws!.send(JSON.stringify(message))
  }

  sendFrame(meta: { seq: number; t_phone_ms: number; w: number; h: number }, blob: Blob): void {
    if (!this.open) return
    this.ws!.send(JSON.stringify({ type: 'frame_meta', ...meta }))
    this.ws!.send(blob)
  }

  pause(): void {
    this.paused = true
    if (this.open) {
      this.send({ type: 'pause' })
      this.set({ state: 'paused' })
    }
  }

  resume(): void {
    this.paused = false
    if (this.open) {
      this.send({ type: 'resume' })
      this.set({ state: 'streaming' })
    }
  }
}
