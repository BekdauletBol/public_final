import { useCallback, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { Dialog } from '../components/Dialog'
import { EventTable } from '../components/EventTable'
import { LiveView } from '../components/LiveView'
import { PinModal } from '../components/PinModal'
import { SettingsForm } from '../components/SettingsForm'
import { ErrorState, Loading } from '../components/States'
import { StatusBadge } from '../components/StatusBadge'
import { ZoneEditor } from '../components/ZoneEditor'
import { cameras, events as eventsApi, zones as zonesApi, type Zone } from '../lib/api'
import { errorMessage } from '../lib/errors'
import { formatRelative } from '../lib/format'
import { useLive } from '../lib/ws'

export function CameraDetail() {
  const { id = '' } = useParams()
  const queryClient = useQueryClient()
  const q = useQuery({ queryKey: ['camera', id], queryFn: () => cameras.get(id) })
  const live = useLive((s) => s.statuses[id])
  const metrics = useLive((s) => s.metrics)
  const liveEvents = useLive((s) => s.events)
  const recent = useQuery({
    queryKey: ['events', { camera_id: id, panel: true }],
    queryFn: () => eventsApi.list({ camera_id: id }, 50),
  })
  const [pinOpen, setPinOpen] = useState(0)
  const [revokeOpen, setRevokeOpen] = useState(false)
  const [editing, setEditing] = useState<{ zone: Zone | null } | null>(null)
  const [frameSize, setFrameSize] = useState<[number, number] | null>(null)
  const onFrameSize = useCallback((w: number, h: number) => setFrameSize([w, h]), [])
  const removeZone = useMutation({
    mutationFn: (zoneId: string) => zonesApi.remove(zoneId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['camera', id] }),
  })
  const toggleZone = useMutation({
    mutationFn: (zone: Zone) => zonesApi.patch(zone.id, { enabled: !zone.enabled }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['camera', id] }),
  })
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

      {c.zones.some((z) => z.needs_review) && (
        <div className="banner banner-inline" role="status">
          Frame size changed since a zone was drawn; verify it.
        </div>
      )}
      <LiveView
        cameraId={c.id}
        maxFps={10}
        className="live-hero"
        label={`Live view of ${c.name}`}
        zones={c.zones}
        overlays={editing === null}
        onFrameSize={onFrameSize}
      >
        {editing && (
          <ZoneEditor
            key={editing.zone?.id ?? 'new'}
            cameraId={c.id}
            frameSize={frameSize ?? [editing.zone?.frame_w ?? 1280, editing.zone?.frame_h ?? 720]}
            existing={editing.zone}
            onDone={() => setEditing(null)}
          />
        )}
      </LiveView>

      <div className="page-head">
        <h2>Zones</h2>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => setEditing({ zone: null })}
          disabled={editing !== null || c.zones.length >= 1}
          title={c.zones.length >= 1 ? 'One zone per camera in this version' : undefined}
        >
          Draw zone
        </button>
      </div>
      {c.zones.length === 0 && !editing && (
        <p className="muted small">No zone yet. Draw one on the live frame to start counting.</p>
      )}
      {c.zones.length > 0 && (
        <ul className="card-list">
          {c.zones.map((z) => (
            <li key={z.id} className="card card-row">
              <div>
                <span className="card-title">{z.name}</span>
                <div className="small muted">
                  {z.points.length} points · rule {z.active_rule} · enter {z.enter_frames} / clear{' '}
                  {z.exit_frames} frames · {z.enabled ? 'enabled' : 'disabled'}
                  {z.needs_review ? ' · ⚠ needs review' : ''}
                </div>
              </div>
              <div className="page-actions">
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => setEditing({ zone: z })}
                >
                  Edit
                </button>
                <button type="button" className="btn btn-sm" onClick={() => toggleZone.mutate(z)}>
                  {z.enabled ? 'Disable' : 'Enable'}
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-quiet"
                  onClick={() => removeZone.mutate(z.id)}
                >
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <section className="metrics">
        <div className="page-head">
          <h2>Events</h2>
          <Link className="btn btn-sm" to={`/events?camera_id=${c.id}`}>
            All events
          </Link>
        </div>
        {(() => {
          const fromQuery = recent.data?.items ?? []
          const ids = new Set(fromQuery.map((e) => e.id))
          const merged = [
            ...liveEvents.filter((e) => e.camera_id === c.id && !ids.has(e.id)),
            ...fromQuery,
          ].slice(0, 50)
          return merged.length === 0 ? (
            <p className="muted small">No events yet.</p>
          ) : (
            <EventTable events={merged} cameraNames={{ [c.id]: c.name }} height={300} />
          )
        })()}
      </section>

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
          <SettingsForm key={JSON.stringify(c.settings)} cameraId={c.id} settings={c.settings} />
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
