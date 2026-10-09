import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { Dialog } from '../components/Dialog'
import { LiveView } from '../components/LiveView'
import { PinModal } from '../components/PinModal'
import { ErrorState, Loading } from '../components/States'
import { StatusBadge } from '../components/StatusBadge'
import { cameras } from '../lib/api'
import { errorMessage } from '../lib/errors'
import { formatRelative } from '../lib/format'
import { useLive } from '../lib/ws'

export function CameraDetail() {
  const { id = '' } = useParams()
  const queryClient = useQueryClient()
  const q = useQuery({ queryKey: ['camera', id], queryFn: () => cameras.get(id) })
  const live = useLive((s) => s.statuses[id])
  const metrics = useLive((s) => s.metrics)
  const [pinOpen, setPinOpen] = useState(0)
  const [revokeOpen, setRevokeOpen] = useState(false)
  const revoke = useMutation({
    mutationFn: () => cameras.revoke(id),
    onSuccess: () => {
      setRevokeOpen(false)
      void queryClient.invalidateQueries({ queryKey: ['camera', id] })
    },
  })

  if (q.isPending) return <Loading />
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  const c = q.data
  const status = live?.status ?? c.status
  const m = metrics?.cameras[c.id]

  return (
    <section>
      <p className="breadcrumb">
        <Link to="/">Sessions</Link> / <Link to={`/sessions/${c.session_id}`}>Session</Link> /{' '}
        {c.name}
      </p>
      <div className="page-head">
        <div>
          <h1>{c.name}</h1>
          <p className="muted small">
            <StatusBadge status={status} /> · last seen{' '}
            {formatRelative(live?.last_seen_at ?? c.last_seen_at)}
            {c.device_label ? ` · ${c.device_label}` : ''}
          </p>
        </div>
        <div className="page-actions">
          <button type="button" className="btn" onClick={() => setPinOpen(Date.now())}>
            Replace phone
          </button>
          {status !== 'revoked' && (
            <button type="button" className="btn" onClick={() => setRevokeOpen(true)}>
              Revoke
            </button>
          )}
        </div>
      </div>

      <LiveView
        cameraId={c.id}
        maxFps={10}
        className="live-hero"
        label={`Live view of ${c.name}`}
      />

      <div className="detail-grid">
        <div className="card">
          <h3>Health</h3>
          <dl className="kv">
            <dt>Input / processed fps</dt>
            <dd>{m ? `${m.fps_in.toFixed(1)} / ${m.fps_processed.toFixed(1)}` : '—'}</dd>
            <dt>Dropped</dt>
            <dd>
              {m
                ? `${(m.drop_ratio * 100).toFixed(1)} % (replaced ${m.dropped_replaced}, rate-limited ${m.dropped_ratelimit})`
                : '—'}
            </dd>
            <dt>RTT / clock offset</dt>
            <dd>
              {m?.rtt_ms != null ? `${Math.round(m.rtt_ms)} ms` : '—'} /{' '}
              {m?.clock_offset_ms != null ? `${Math.round(m.clock_offset_ms)} ms` : '—'}
            </dd>
            <dt>Total p50 / p99</dt>
            <dd>
              {m?.spans.total.p50 != null ? `${Math.round(m.spans.total.p50)} ms` : '—'} /{' '}
              {m?.spans.total.p99 != null ? `${Math.round(m.spans.total.p99)} ms` : '—'}
            </dd>
            <dt>Frame size</dt>
            <dd>{m?.last_w ? `${m.last_w} × ${m.last_h}` : '—'}</dd>
            <dt>Decode / worker errors</dt>
            <dd>{m ? `${m.decode_errors} / ${m.worker_errors}` : '—'}</dd>
          </dl>
        </div>
        <div className="card">
          <h3>Settings</h3>
          <dl className="kv">
            <dt>Target fps</dt>
            <dd>{c.settings.target_fps}</dd>
            <dt>JPEG quality</dt>
            <dd>{c.settings.jpeg_quality}</dd>
            <dt>Max long edge</dt>
            <dd>{c.settings.max_long_edge}</dd>
            <dt>Confidence</dt>
            <dd>{c.settings.confidence}</dd>
            <dt>Privacy mode</dt>
            <dd>{c.settings.privacy_mode ? 'on (no previews)' : 'off'}</dd>
          </dl>
          <p className="muted small">The settings form and zone editor arrive in M4.</p>
        </div>
      </div>

      <PinModal
        key={pinOpen}
        open={pinOpen > 0}
        sessionId={c.session_id}
        replaceCameraId={c.id}
        replaceCameraName={c.name}
        onClose={() => {
          setPinOpen(0)
          void queryClient.invalidateQueries({ queryKey: ['camera', id] })
        }}
      />
      <Dialog open={revokeOpen} title="Revoke phone?" onClose={() => setRevokeOpen(false)}>
        <p>
          The phone paired as <strong>{c.name}</strong> will be disconnected and its token
          invalidated.
        </p>
        {revoke.isError && (
          <p className="form-error" role="alert">
            {errorMessage(revoke.error)}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={() => setRevokeOpen(false)}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => revoke.mutate()}
            disabled={revoke.isPending}
          >
            Revoke
          </button>
        </div>
      </Dialog>
    </section>
  )
}
