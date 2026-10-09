import { useEffect, useState } from 'react'

type Health = {
  status: string
  version: string
  uptime_s: number
  device: string | null
  model_sha256: string | null
  db: string | null
}

type State =
  { kind: 'loading' } | { kind: 'ok'; health: Health } | { kind: 'error'; message: string }

export default function App() {
  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/health', { signal: controller.signal })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        setState({ kind: 'ok', health: (await r.json()) as Health })
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted) return
        setState({ kind: 'error', message: e instanceof Error ? e.message : String(e) })
      })
    return () => controller.abort()
  }, [])

  return (
    <main className="shell">
      <h1>public.safe</h1>
      <p className="muted">M0 scaffold. Dashboard pages arrive in the next milestones.</p>
      <section className="card" aria-live="polite">
        <h2>Backend</h2>
        {state.kind === 'loading' && <p>Checking /api/health…</p>}
        {state.kind === 'error' && (
          <p role="alert">
            Backend unreachable ({state.message}). Is <code>make dev</code> running?
          </p>
        )}
        {state.kind === 'ok' && (
          <dl>
            <dt>Status</dt>
            <dd>{state.health.status}</dd>
            <dt>Version</dt>
            <dd>{state.health.version}</dd>
            <dt>Uptime</dt>
            <dd>{state.health.uptime_s} s</dd>
            <dt>Secure context</dt>
            <dd>{window.isSecureContext ? 'yes' : 'no (use https)'}</dd>
          </dl>
        )}
      </section>
    </main>
  )
}
