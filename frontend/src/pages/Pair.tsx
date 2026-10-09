import {
  useEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError, api } from '../lib/api'
import {
  forgetCredentials,
  loadCredentials,
  saveCredentials,
  type PhoneCredentials,
} from '../lib/phone'

const ALPHABET = /[BCDFGHJKLMNPQRSTVWXZ0-9]/

interface RedeemOut {
  camera_id: string
  camera_name: string
  session_name: string
  device_token: string
  ws_url: string
}

type Precheck =
  | { kind: 'checking' }
  | { kind: 'insecure'; httpsUrl: string }
  | { kind: 'no-media' }
  | { kind: 'unreachable'; message: string }
  | { kind: 'ok'; rttMs: number }

function usePrecheck(): Precheck {
  const [state, setState] = useState<Precheck>({ kind: 'checking' })
  useEffect(() => {
    let cancelled = false
    const run = async () => {
      if (!window.isSecureContext) {
        const url = `https://${window.location.host}${window.location.pathname}${window.location.search}`
        setState({ kind: 'insecure', httpsUrl: url })
        return
      }
      if (!navigator.mediaDevices?.getUserMedia) {
        setState({ kind: 'no-media' })
        return
      }
      const t0 = performance.now()
      try {
        await api.get<{ server_time_ms: number; version: string }>('/api/pair/precheck')
        if (!cancelled) setState({ kind: 'ok', rttMs: Math.round(performance.now() - t0) })
      } catch (e) {
        if (!cancelled)
          setState({ kind: 'unreachable', message: e instanceof Error ? e.message : String(e) })
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [])
  return state
}

export function Pair() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const codeId = params.get('c')
  const precheck = usePrecheck()
  const [existing, setExisting] = useState<PhoneCredentials | null>(() => loadCredentials())
  const [digits, setDigits] = useState<string[]>(Array(6).fill(''))
  const [remember, setRemember] = useState(false)
  const [label, setLabel] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [retryIn, setRetryIn] = useState(0)
  const [busy, setBusy] = useState(false)
  const inputs = useRef<(HTMLInputElement | null)[]>([])

  useEffect(() => {
    if (retryIn <= 0) return
    const id = window.setTimeout(() => setRetryIn((s) => s - 1), 1000)
    return () => window.clearTimeout(id)
  }, [retryIn])

  const setDigit = (index: number, value: string) => {
    const ch = value.toUpperCase().slice(-1)
    if (ch && !ALPHABET.test(ch)) return
    setDigits((d) => {
      const next = [...d]
      next[index] = ch
      return next
    })
    if (ch && index < 5) inputs.current[index + 1]?.focus()
  }

  const onKey = (index: number, e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Backspace' && !digits[index] && index > 0) {
      inputs.current[index - 1]?.focus()
    }
  }

  const onPaste = (e: ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData
      .getData('text')
      .toUpperCase()
      .replace(/[^A-Z0-9]/g, '')
      .slice(0, 6)
    if (!text) return
    e.preventDefault()
    const next = Array(6).fill('') as string[]
    text.split('').forEach((ch, i) => {
      next[i] = ch
    })
    setDigits(next)
    inputs.current[Math.min(text.length, 5)]?.focus()
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (busy || retryIn > 0) return
    const pin = digits.join('')
    if (pin.length < 6) {
      setError('Enter all six characters.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const out = await api.post<RedeemOut>('/api/pair/redeem', {
        pin,
        code_id: codeId ?? undefined,
        device_label: label.trim() || undefined,
        remember,
      })
      saveCredentials({ ...out, remember })
      navigate('/camera', { replace: true })
    } catch (err) {
      if (err instanceof ApiError) {
        const messages: Record<string, string> = {
          pin_invalid: 'That code is not valid. Check it and try again.',
          pin_expired: 'This code has expired. Ask the admin for a new one.',
          pin_locked: 'Too many wrong attempts. Ask the admin to generate a new code.',
          pin_already_redeemed: 'This code was already used by another phone.',
          session_gone: 'This monitoring session is no longer accepting phones.',
          network_error: 'Cannot reach the server. Check the Wi-Fi and the URL.',
        }
        if (err.status === 429) {
          setRetryIn(err.retryAfterS ?? 60)
          setError(null)
        } else {
          setError(messages[err.code] ?? err.message)
        }
      } else {
        setError(String(err))
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="phone">
      <h1>public.safe</h1>
      <p className="muted">Pair this phone as a camera</p>

      {precheck.kind === 'checking' && <p className="card">Checking this browser…</p>}
      {precheck.kind === 'insecure' && (
        <div className="card phone-block" role="alert">
          <p>
            <strong>This page must be opened over HTTPS.</strong> Browsers only allow camera access
            in a secure context.
          </p>
          <p>Open this address instead:</p>
          <p className="mono small">{precheck.httpsUrl}</p>
          <p>
            If the browser warns about the certificate, install the root certificate first:{' '}
            <a href="/help/phone">phone setup guide</a>.
          </p>
        </div>
      )}
      {precheck.kind === 'no-media' && (
        <div className="card phone-block" role="alert">
          <p>
            <strong>This browser cannot access the camera.</strong> Use Safari on iOS or Chrome on
            Android.
          </p>
        </div>
      )}
      {precheck.kind === 'unreachable' && (
        <div className="card phone-block" role="alert">
          <p>
            <strong>The server did not answer</strong> ({precheck.message}). Make sure the phone is
            on the same Wi-Fi as the laptop.
          </p>
        </div>
      )}

      {precheck.kind === 'ok' && existing && (
        <div className="card phone-block">
          <p>
            This phone is already paired as <strong>{existing.camera_name}</strong> in{' '}
            {existing.session_name}.
          </p>
          <div className="phone-actions">
            <button type="button" className="btn btn-primary" onClick={() => navigate('/camera')}>
              Continue streaming
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => {
                forgetCredentials()
                setExisting(null)
              }}
            >
              Forget and pair again
            </button>
          </div>
        </div>
      )}

      {precheck.kind === 'ok' && !existing && (
        <form className="card phone-block form" onSubmit={(e) => void submit(e)}>
          <p className="small muted">
            Type the 6-character code shown on the dashboard. Server reachable ({precheck.rttMs}{' '}
            ms).
          </p>
          <div className="pin-boxes" role="group" aria-label="Pairing code">
            {digits.map((d, i) => (
              <input
                key={i}
                ref={(el) => {
                  inputs.current[i] = el
                }}
                className="pin-box"
                value={d}
                onChange={(e) => setDigit(i, e.target.value)}
                onKeyDown={(e) => onKey(i, e)}
                onPaste={onPaste}
                inputMode="text"
                autoCapitalize="characters"
                autoComplete="one-time-code"
                maxLength={1}
                aria-label={`Character ${i + 1}`}
                autoFocus={i === 0}
              />
            ))}
          </div>
          <label>
            Phone name (optional)
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              maxLength={60}
              placeholder="e.g. iPhone at the gate"
            />
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
            />
            Remember this phone (keeps the pairing after the browser closes)
          </label>
          <p className="form-error" role="alert" aria-live="assertive">
            {retryIn > 0 ? `Too many attempts. Try again in ${retryIn} s.` : error}
          </p>
          <button type="submit" className="btn btn-primary btn-big" disabled={busy || retryIn > 0}>
            {busy ? 'Pairing…' : 'Pair'}
          </button>
        </form>
      )}
    </main>
  )
}
