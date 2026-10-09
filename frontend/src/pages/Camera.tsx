import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CaptureLoop, type CaptureStats } from '../lib/capture'
import { forgetCredentials, loadCredentials, PhoneSocket, type PhoneStatus } from '../lib/phone'
import { formatRelative } from '../lib/format'

type CameraError =
  { kind: 'denied' } | { kind: 'not-found' } | { kind: 'busy' } | { kind: 'other'; message: string }

const CONSTRAINTS: MediaStreamConstraints = {
  video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
  audio: false,
}

function describeStop(status: PhoneStatus): string {
  switch (status.stopCode) {
    case 4003:
      return 'This phone was revoked by the admin. Its pairing was removed; pair again with a new code.'
    case 4004:
      return 'Another tab took over this camera. Close this tab or reload to take it back.'
    case 4008:
      return 'The server closed the connection because frames arrived too fast. Reload to retry.'
    default:
      return status.reason ?? 'Stopped.'
  }
}

export function Camera() {
  const navigate = useNavigate()
  const [creds] = useState(() => loadCredentials())
  const videoRef = useRef<HTMLVideoElement>(null)
  const [status, setStatus] = useState<PhoneStatus | null>(null)
  const [stats, setStats] = useState<CaptureStats | null>(null)
  const [camError, setCamError] = useState<CameraError | null>(null)
  const [wakeLock, setWakeLock] = useState<'active' | 'unsupported' | 'released'>('unsupported')
  const [confirmForget, setConfirmForget] = useState(false)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    if (!creds) navigate('/pair', { replace: true })
  }, [creds, navigate])

  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), 1000)
    return () => window.clearInterval(id)
  }, [])

  useEffect(() => {
    if (!creds || !videoRef.current) return
    const video = videoRef.current
    let stream: MediaStream | null = null
    let socket: PhoneSocket | null = null
    let loop: CaptureLoop | null = null
    let lock: WakeLockSentinel | null = null
    let disposed = false

    const requestWakeLock = async () => {
      if (!('wakeLock' in navigator)) {
        setWakeLock('unsupported')
        return
      }
      try {
        lock = await navigator.wakeLock.request('screen')
        setWakeLock('active')
        lock.addEventListener('release', () => setWakeLock('released'))
      } catch {
        setWakeLock('released')
      }
    }

    const onVisibility = () => {
      if (document.visibilityState === 'hidden') {
        socket?.pause()
        loop?.stop()
      } else {
        socket?.resume()
        loop?.start()
        void requestWakeLock()
      }
    }

    const run = async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia(CONSTRAINTS)
      } catch (e) {
        const name = e instanceof DOMException ? e.name : ''
        if (name === 'NotAllowedError') setCamError({ kind: 'denied' })
        else if (name === 'NotFoundError') setCamError({ kind: 'not-found' })
        else if (name === 'NotReadableError') setCamError({ kind: 'busy' })
        else if (name === 'OverconstrainedError') {
          try {
            stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false })
          } catch {
            setCamError({ kind: 'other', message: 'No usable camera.' })
          }
        } else setCamError({ kind: 'other', message: e instanceof Error ? e.message : String(e) })
      }
      if (!stream || disposed) return
      video.srcObject = stream
      await video.play().catch(() => undefined)

      socket = new PhoneSocket(creds, {
        ua: navigator.userAgent.slice(0, 120),
        w: video.videoWidth,
        h: video.videoHeight,
        remember: creds.remember,
      })
      socket.on((s) => {
        setStatus(s)
        if (loop && s.settings) loop.settings = s.settings
        if (s.state === 'streaming') loop?.start()
        if (s.state === 'stopped') loop?.stop()
      })
      loop = new CaptureLoop(video, socket, {
        target_fps: 10,
        jpeg_quality: 65,
        max_long_edge: 1280,
        confidence: 0.3,
        privacy_mode: false,
      })
      loop.on(setStats)
      socket.start()
      document.addEventListener('visibilitychange', onVisibility)
      await requestWakeLock()
    }
    void run()

    return () => {
      disposed = true
      document.removeEventListener('visibilitychange', onVisibility)
      loop?.stop()
      socket?.stop()
      stream?.getTracks().forEach((t) => t.stop())
      void lock?.release()
    }
  }, [creds])

  if (!creds) return null
  const host = creds.ws_url.replace(/^wss?:\/\//, '').replace(/\/ws\/camera$/, '')
  const state = status?.state ?? 'connecting'
  const stateLabel: Record<string, string> = {
    idle: 'Starting',
    connecting: 'Connecting',
    streaming: 'Streaming',
    paused: 'Paused (tab hidden)',
    reconnecting: `Reconnecting (${status?.attempt ?? 0})`,
    stopped: 'Stopped',
  }
  void tick

  return (
    <main className="phone">
      <div className="phone-preview">
        <video ref={videoRef} playsInline muted autoPlay />
      </div>
      <div className="phone-status" aria-live="polite">
        <div className="phone-state">
          <span className={`dot dot-${state}`} aria-hidden="true" />
          <strong>{stateLabel[state] ?? state}</strong>
          {status?.state === 'stopped' && <p className="warn">{describeStop(status)}</p>}
        </div>
        <dl className="kv">
          <dt>Camera</dt>
          <dd>
            {status?.cameraName ?? creds.camera_name} · {creds.session_name}
          </dd>
          <dt>Send rate</dt>
          <dd>
            {stats ? `${stats.sendFps.toFixed(1)} fps` : '—'}
            {stats?.width ? ` · ${stats.width}×${stats.height}` : ''}
            {stats && stats.lastFrameBytes
              ? ` · ${Math.round(stats.lastFrameBytes / 1024)} KB`
              : ''}
          </dd>
          <dt>Last ack</dt>
          <dd>
            {status?.lastAckAt
              ? `${formatRelative(new Date(status.lastAckAt).toISOString())} (seq ${status.lastAckSeq})`
              : 'none yet'}
          </dd>
          <dt>Round trip</dt>
          <dd>{status?.rttMs != null ? `${status.rttMs} ms` : '—'}</dd>
          <dt>Screen</dt>
          <dd>
            {wakeLock === 'active' ? 'kept awake' : 'keep the screen on (wake lock unavailable)'}
          </dd>
        </dl>
      </div>

      {camError && (
        <div className="card phone-block" role="alert">
          {camError.kind === 'denied' && (
            <p>
              <strong>Camera access was denied.</strong> iOS: Settings › Safari › Camera › Allow.
              Android Chrome: tap the lock icon › Site settings › Camera › Allow. Then reload.
            </p>
          )}
          {camError.kind === 'not-found' && <p>No camera found on this device.</p>}
          {camError.kind === 'busy' && (
            <p>The camera is used by another app. Close it and reload.</p>
          )}
          {camError.kind === 'other' && <p>{camError.message}</p>}
        </div>
      )}

      <p className="phone-notice" role="note">
        This phone is streaming video to {host}.
      </p>

      <div className="phone-actions">
        {!confirmForget ? (
          <button type="button" className="btn" onClick={() => setConfirmForget(true)}>
            Forget this phone
          </button>
        ) : (
          <>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                forgetCredentials()
                navigate('/pair', { replace: true })
              }}
            >
              Yes, forget and stop
            </button>
            <button type="button" className="btn" onClick={() => setConfirmForget(false)}>
              Keep
            </button>
          </>
        )}
      </div>
    </main>
  )
}
