import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { CameraTile } from '../components/CameraTile'
import { Dialog } from '../components/Dialog'
import { EventTable } from '../components/EventTable'
import { MetricsPanel } from '../components/MetricsPanel'
import { PinModal } from '../components/PinModal'
import { EmptyState, ErrorState, Loading } from '../components/States'
import { errorMessage } from '../lib/errors'
import {
  cameras,
  events as eventsApi,
  sessions,
  type CameraSummary,
  type SessionDetail as SessionDetailData,
} from '../lib/api'
import { useLive } from '../lib/ws'

export function SessionDetail() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [dialog, setDialog] = useState<'rename' | 'delete' | null>(null)
  const [pin, setPin] = useState<{ open: boolean; replace?: CameraSummary; nonce: number }>({
    open: false,
    nonce: 0,
  })
  const [revoking, setRevoking] = useState<CameraSummary | null>(null)

  const q = useQuery({ queryKey: ['session', id], queryFn: () => sessions.get(id) })
  const recent = useQuery({
    queryKey: ['events', { session_id: id, panel: true }],
    queryFn: () => eventsApi.list({ session_id: id }, 50),
  })
  const liveEvents = useLive((s) => s.events)
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['session', id] })
    void queryClient.invalidateQueries({ queryKey: ['sessions'] })
  }
  const archive = useMutation({
    mutationFn: (archived: boolean) => sessions.patch(id, { archived }),
    onSuccess: invalidate,
  })
  const revoke = useMutation({
    mutationFn: (cameraId: string) => cameras.revoke(cameraId),
    onSuccess: () => {
      setRevoking(null)
      invalidate()
    },
  })
  const remove = useMutation({
    mutationFn: () => sessions.remove(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      navigate('/', { replace: true })
    },
  })

  if (q.isPending) return <Loading />
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  const s = q.data

  return (
    <section>
      <p className="breadcrumb">
        <Link to="/">Sessions</Link> / {s.name}
      </p>
      <div className="page-head">
        <div>
          <h1>{s.name}</h1>
          <p className="muted">
            {s.location_note || 'No location note'}
            {s.archived_at ? ' · archived' : ''}
          </p>
        </div>
        <div className="page-actions">
          <button type="button" className="btn" onClick={() => setDialog('rename')}>
            Rename
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => archive.mutate(!s.archived_at)}
            disabled={archive.isPending}
          >
            {s.archived_at ? 'Unarchive' : 'Archive'}
          </button>
          <button type="button" className="btn" onClick={() => setDialog('delete')}>
            Delete
          </button>
        </div>
      </div>
      {archive.isError && (
        <p className="form-error" role="alert">
          {errorMessage(archive.error)}
        </p>
      )}

      <div className="page-head">
        <h2>Cameras</h2>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => setPin({ open: true, nonce: Date.now() })}
          disabled={s.archived_at !== null}
          title={s.archived_at ? 'Unarchive the session to add cameras' : undefined}
        >
          Add camera
        </button>
      </div>
      {s.cameras.length === 0 ? (
        <EmptyState
          message="No cameras yet — add one to mint a pairing PIN."
          action={
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setPin({ open: true, nonce: Date.now() })}
              disabled={s.archived_at !== null}
            >
              Add camera
            </button>
          }
        />
      ) : (
        <div className="camera-grid">
          {s.cameras.map((c) => (
            <CameraTile
              key={c.id}
              camera={c}
              onReplace={(cam) => setPin({ open: true, replace: cam, nonce: Date.now() })}
              onRevoke={(cam) => setRevoking(cam)}
            />
          ))}
        </div>
      )}

      <section className="metrics">
        <div className="page-head">
          <h2>Events</h2>
          <Link className="btn btn-sm" to="/events">
            All events
          </Link>
        </div>
        {(() => {
          const fromQuery = recent.data?.items ?? []
          const ids = new Set(fromQuery.map((e) => e.id))
          const merged = [
            ...liveEvents.filter((e) => e.session_id === id && !ids.has(e.id)),
            ...fromQuery,
          ].slice(0, 50)
          const names = Object.fromEntries(s.cameras.map((c) => [c.id, c.name]))
          return merged.length === 0 ? (
            <p className="muted small">No events yet.</p>
          ) : (
            <EventTable events={merged} cameraNames={names} height={300} />
          )
        })()}
      </section>

      <MetricsPanel cameras={s.cameras} />

      <PinModal
        key={pin.nonce}
        open={pin.open}
        sessionId={s.id}
        replaceCameraId={pin.replace?.id}
        replaceCameraName={pin.replace?.name}
        onClose={() => {
          setPin({ open: false, nonce: 0 })
          invalidate()
        }}
      />
      <Dialog open={revoking !== null} title="Revoke phone?" onClose={() => setRevoking(null)}>
        <p>
          The phone paired as <strong>{revoking?.name}</strong> will be disconnected and its token
          invalidated. Zones and events stay; use “Replace phone” to pair a new one later.
        </p>
        {revoke.isError && (
          <p className="form-error" role="alert">
            {errorMessage(revoke.error)}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={() => setRevoking(null)}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => revoking && revoke.mutate(revoking.id)}
            disabled={revoke.isPending}
          >
            Revoke
          </button>
        </div>
      </Dialog>

      <RenameDialog
        open={dialog === 'rename'}
        session={s}
        onClose={() => setDialog(null)}
        onSaved={() => {
          setDialog(null)
          invalidate()
        }}
      />
      <Dialog open={dialog === 'delete'} title="Delete session?" onClose={() => setDialog(null)}>
        <p>
          This permanently deletes <strong>{s.name}</strong>, its {s.camera_count}{' '}
          {s.camera_count === 1 ? 'camera' : 'cameras'}, all zones, events and metrics, and revokes
          the paired phones. Archive instead to keep the data.
        </p>
        {remove.isError && (
          <p className="form-error" role="alert">
            {errorMessage(remove.error)}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={() => setDialog(null)}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => remove.mutate()}
            disabled={remove.isPending}
          >
            Delete permanently
          </button>
        </div>
      </Dialog>
    </section>
  )
}

function RenameDialog({
  open,
  session,
  onClose,
  onSaved,
}: {
  open: boolean
  session: SessionDetailData
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(session.name)
  const [note, setNote] = useState(session.location_note ?? '')
  const save = useMutation({
    mutationFn: () => sessions.patch(session.id, { name, location_note: note || null }),
    onSuccess: onSaved,
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    save.mutate()
  }
  return (
    <Dialog open={open} title="Rename session" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <label>
          Name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={80}
            required
            autoFocus
          />
        </label>
        <label>
          Location note
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={200} />
        </label>
        {save.isError && (
          <p className="form-error" role="alert">
            {errorMessage(save.error)}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={save.isPending}>
            Save
          </button>
        </div>
      </form>
    </Dialog>
  )
}
