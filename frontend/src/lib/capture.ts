// Phone capture loop (F-PHONE-3): grab video frames at the target fps, scale to the max long
// edge, encode JPEG, send over the socket. Never buffers more than one unsent frame.

import type { CaptureSettings, PhoneSocket } from './phone'

export interface CaptureStats {
  sent: number
  skipped: number
  sendFps: number
  lastFrameBytes: number
  width: number
  height: number
}

type StatsListener = (stats: CaptureStats) => void

export class CaptureLoop {
  private canvas = document.createElement('canvas')
  private ctx = this.canvas.getContext('2d', { alpha: false })
  private running = false
  private timer: number | null = null
  private rvfcHandle: number | null = null
  private seq = 0
  private lastSendAt = 0
  private encoding = false
  private sentTimes: number[] = []
  private listeners = new Set<StatsListener>()
  stats: CaptureStats = { sent: 0, skipped: 0, sendFps: 0, lastFrameBytes: 0, width: 0, height: 0 }

  private readonly video: HTMLVideoElement
  private readonly socket: PhoneSocket
  settings: CaptureSettings

  constructor(video: HTMLVideoElement, socket: PhoneSocket, settings: CaptureSettings) {
    this.video = video
    this.socket = socket
    this.settings = settings
  }

  on(listener: StatsListener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private emit(): void {
    const now = performance.now()
    this.sentTimes = this.sentTimes.filter((t) => now - t < 2000)
    this.stats = { ...this.stats, sendFps: this.sentTimes.length / 2 }
    this.listeners.forEach((l) => l(this.stats))
  }

  start(): void {
    if (this.running) return
    this.running = true
    this.schedule()
  }

  stop(): void {
    this.running = false
    if (this.timer) window.clearInterval(this.timer)
    this.timer = null
    if (this.rvfcHandle !== null && 'cancelVideoFrameCallback' in this.video) {
      ;(
        this.video as HTMLVideoElement & { cancelVideoFrameCallback: (h: number) => void }
      ).cancelVideoFrameCallback(this.rvfcHandle)
    }
    this.rvfcHandle = null
  }

  private schedule(): void {
    const video = this.video as HTMLVideoElement & {
      requestVideoFrameCallback?: (cb: () => void) => number
    }
    if (typeof video.requestVideoFrameCallback === 'function') {
      const tick = () => {
        if (!this.running) return
        void this.maybeCapture()
        this.rvfcHandle = video.requestVideoFrameCallback!(tick)
      }
      this.rvfcHandle = video.requestVideoFrameCallback(tick)
    } else {
      this.timer = window.setInterval(() => void this.maybeCapture(), 1000 / 60)
    }
  }

  private async maybeCapture(): Promise<void> {
    const interval = 1000 / Math.max(1, this.settings.target_fps)
    const now = performance.now()
    if (now - this.lastSendAt < interval - 1 || this.encoding) return
    if (this.video.readyState < 2 || this.video.videoWidth === 0) return
    // Back-pressure: skip when more than two frames' worth of bytes are unsent.
    const limit = Math.max(2 * this.stats.lastFrameBytes, 64 * 1024)
    if (!this.socket.open || this.socket.bufferedAmount > limit) {
      this.stats = { ...this.stats, skipped: this.stats.skipped + 1 }
      this.emit()
      return
    }
    this.lastSendAt = now
    this.encoding = true
    try {
      const blob = await this.encode()
      if (!blob || !this.running) return
      this.seq += 1
      this.socket.sendFrame(
        { seq: this.seq, t_phone_ms: Date.now(), w: this.canvas.width, h: this.canvas.height },
        blob,
      )
      this.sentTimes.push(performance.now())
      this.stats = {
        ...this.stats,
        sent: this.stats.sent + 1,
        lastFrameBytes: blob.size,
        width: this.canvas.width,
        height: this.canvas.height,
      }
      this.emit()
    } finally {
      this.encoding = false
    }
  }

  private encode(): Promise<Blob | null> {
    const vw = this.video.videoWidth
    const vh = this.video.videoHeight
    const long = Math.max(vw, vh)
    const scale = long > this.settings.max_long_edge ? this.settings.max_long_edge / long : 1
    const w = Math.round(vw * scale)
    const h = Math.round(vh * scale)
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w
      this.canvas.height = h
    }
    if (!this.ctx) return Promise.resolve(null)
    this.ctx.drawImage(this.video, 0, 0, w, h)
    const quality = Math.min(0.95, Math.max(0.3, this.settings.jpeg_quality / 100))
    return new Promise((resolve) => this.canvas.toBlob(resolve, 'image/jpeg', quality))
  }
}
