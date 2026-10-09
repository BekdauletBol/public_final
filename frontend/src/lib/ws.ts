// Dashboard WebSocket store (F.8, J.4): one connection per tab, exponential backoff
// 1 s .. 30 s with +-20 % jitter, state readable through useSyncExternalStore.

import { useSyncExternalStore } from 'react'
import { UNAUTHENTICATED_EVENT, type CameraStatus } from './api'
import { frameStore, type FrameResultMsg } from './frames'

export interface CameraStatusMessage {
  type: 'camera_status'
  camera_id: string
  session_id: string
  status: CameraStatus
  last_seen_at: string | null
  reason: string | null
}

export type Percentiles = {
  p50: number | null
  p90: number | null
  p95: number | null
  p99: number | null
}

export interface CameraMetrics {
  status: CameraStatus
  connected_s: number | null
  last_seen_at: string | null
  frames_in: number
  bytes_in: number
  processed: number
  processed_seq: number | null
  dropped_replaced: number
  dropped_ratelimit: number
  decode_errors: number
  worker_errors: number
  out_of_order: number
  rtt_ms: number | null
  clock_offset_ms: number | null
  clock_offset_uncertainty_ms: number | null
  fps_in: number
  fps_processed: number
  drop_ratio: number
  last_w: number | null
  last_h: number | null
  target_fps: number | null
  privacy_mode: boolean | null
  spans: {
    total: Percentiles
    infer: Percentiles
    decode: Percentiles
    queue_wait: Percentiles
    e2e_estimate: Percentiles
  }
}

export interface MetricsMessage {
  type: 'metrics'
  t_server_ms: number
  server: {
    version: string
    uptime_s: number
    cpu_percent: number
    mem_mb: number
    device: string | null
    model_sha256: string | null
    active_cameras: number
    dashboards: number
    scheduler_strategy: string
    scheduler_backlog: number
    eval_record: boolean
  }
  cameras: Record<string, CameraMetrics>
}

export type ServerMessage =
  | CameraStatusMessage
  | MetricsMessage
  | { type: 'error'; code: string; message: string }
  | { type: string; [key: string]: unknown }

export interface LiveState {
  connected: boolean
  attempt: number
  everConnected: boolean
  statuses: Record<
    string,
    { status: CameraStatus; last_seen_at: string | null; session_id: string }
  >
  metrics: MetricsMessage | null
}

type Listener = (message: ServerMessage) => void

const MAX_DELAY_MS = 30_000

export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(MAX_DELAY_MS, 1000 * 2 ** Math.max(0, attempt - 1))
  const jitter = 0.8 + 0.4 * random()
  return Math.round(base * jitter)
}

class DashboardSocket {
  private ws: WebSocket | null = null
  private timer: number | null = null
  private stopTimer: number | null = null
  private listeners = new Set<Listener>()
  private stateListeners = new Set<() => void>()
  private wanted = false
  private pendingPreview: { camera_id: string; seq: number } | null = null
  state: LiveState = {
    connected: false,
    attempt: 0,
    everConnected: false,
    statuses: {},
    metrics: null,
  }

  start(): void {
    if (this.stopTimer !== null) {
      // A stop was scheduled a moment ago (React StrictMode re-runs effects): keep the socket.
      window.clearTimeout(this.stopTimer)
      this.stopTimer = null
    }
    if (this.wanted) return
    this.wanted = true
    this.open()
  }

  /** Deferred so that an unmount/remount pair does not abort a connecting socket. */
  stop(): void {
    if (this.stopTimer !== null) return
    this.stopTimer = window.setTimeout(() => {
      this.stopTimer = null
      this.stopNow()
    }, 0)
  }

  stopNow(): void {
    this.wanted = false
    if (this.timer) window.clearTimeout(this.timer)
    this.timer = null
    const ws = this.ws
    this.ws = null
    ws?.close(1000, 'client_stop')
    if (this.state.connected || this.state.attempt !== 0) {
      this.setState({ connected: false, attempt: 0 })
    }
  }

  on(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  subscribeState = (listener: () => void): (() => void) => {
    this.stateListeners.add(listener)
    return () => this.stateListeners.delete(listener)
  }

  getState = (): LiveState => this.state

  private setState(patch: Partial<LiveState>): void {
    this.state = { ...this.state, ...patch }
    this.stateListeners.forEach((l) => l())
  }

  private open(): void {
    if (!this.wanted) return
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${scheme}://${window.location.host}/ws/dashboard`)
    ws.binaryType = 'arraybuffer'
    this.ws = ws
    ws.onopen = () => {
      this.setState({ connected: true, attempt: 0, everConnected: true })
      ws.send(JSON.stringify({ type: 'subscribe', camera_ids: 'all', preview_fps: 10 }))
    }
    ws.onmessage = (event) => {
      if (typeof event.data !== 'string') {
        const pending = this.pendingPreview
        this.pendingPreview = null
        if (pending && event.data instanceof ArrayBuffer) {
          frameStore.setFrame(
            pending.camera_id,
            new Blob([event.data], { type: 'image/jpeg' }),
            pending.seq,
          )
        }
        return
      }
      let message: ServerMessage
      try {
        message = JSON.parse(event.data) as ServerMessage
      } catch {
        return
      }
      this.handle(message)
      this.listeners.forEach((l) => l(message))
    }
    ws.onclose = (event) => {
      if (this.ws !== ws) return
      this.ws = null
      this.setState({ connected: false })
      if (event.code === 4001) {
        this.wanted = false
        window.dispatchEvent(new CustomEvent(UNAUTHENTICATED_EVENT))
        return
      }
      if (!this.wanted) return
      const attempt = this.state.attempt + 1
      this.setState({ attempt })
      this.timer = window.setTimeout(() => this.open(), backoffDelay(attempt))
    }
    ws.onerror = () => {
      // onclose follows and schedules the retry
    }
  }

  private handle(message: ServerMessage): void {
    if (message.type === 'camera_status') {
      const m = message as CameraStatusMessage
      this.setState({
        statuses: {
          ...this.state.statuses,
          [m.camera_id]: {
            status: m.status,
            last_seen_at: m.last_seen_at,
            session_id: m.session_id,
          },
        },
      })
    } else if (message.type === 'metrics') {
      this.setState({ metrics: message as MetricsMessage })
    } else if (message.type === 'frame_preview') {
      const m = message as unknown as { camera_id: string; seq: number }
      this.pendingPreview = { camera_id: m.camera_id, seq: m.seq }
    } else if (message.type === 'frame_result') {
      frameStore.setResult(message as unknown as FrameResultMsg)
    }
  }
}

export const dashboardSocket = new DashboardSocket()

/**
 * Read a slice of the live state. The selector MUST return a primitive or a
 * reference that is stable between store updates (e.g. `s.connected`,
 * `s.statuses[id]`); building a new object per call makes React re-render forever.
 */
export function useLive<T>(selector: (state: LiveState) => T): T {
  return useSyncExternalStore(dashboardSocket.subscribeState, () =>
    selector(dashboardSocket.getState()),
  )
}
