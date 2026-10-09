// Dashboard WebSocket store (F.8, J.4): one connection per tab, exponential backoff
// 1 s .. 30 s with +-20 % jitter, state readable through useSyncExternalStore.

import { useSyncExternalStore } from 'react'
import { UNAUTHENTICATED_EVENT, type CameraStatus } from './api'

export interface CameraStatusMessage {
  type: 'camera_status'
  camera_id: string
  session_id: string
  status: CameraStatus
  last_seen_at: string | null
  reason: string | null
}

export interface CameraMetrics {
  status: CameraStatus
  connected_s: number
  last_seen_at: string | null
  frames_in: number
  bytes_in: number
  out_of_order: number
  rtt_ms: number | null
  clock_offset_ms: number | null
  clock_offset_uncertainty_ms: number | null
  fps_in: number | null
  fps_processed: number | null
  drop_ratio: number | null
  spans: Record<string, number | null> | null
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
    active_cameras: number
    dashboards: number
    scheduler_strategy: string
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
  private listeners = new Set<Listener>()
  private stateListeners = new Set<() => void>()
  private wanted = false
  state: LiveState = {
    connected: false,
    attempt: 0,
    everConnected: false,
    statuses: {},
    metrics: null,
  }

  start(): void {
    if (this.wanted) return
    this.wanted = true
    this.open()
  }

  stop(): void {
    this.wanted = false
    if (this.timer) window.clearTimeout(this.timer)
    this.timer = null
    this.ws?.close(1000, 'client_stop')
    this.ws = null
    this.setState({ connected: false, attempt: 0 })
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
    this.ws = ws
    ws.onopen = () => {
      this.setState({ connected: true, attempt: 0, everConnected: true })
      ws.send(JSON.stringify({ type: 'subscribe', camera_ids: 'all', preview_fps: 10 }))
    }
    ws.onmessage = (event) => {
      if (typeof event.data !== 'string') return // binary previews arrive in M3
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
    }
  }
}

export const dashboardSocket = new DashboardSocket()

export function useLive<T>(selector: (state: LiveState) => T): T {
  return useSyncExternalStore(dashboardSocket.subscribeState, () =>
    selector(dashboardSocket.getState()),
  )
}
