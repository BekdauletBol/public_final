import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Dialog } from '../components/Dialog'
import { EmptyState, ErrorState, Loading } from '../components/States'
import { errorMessage } from '../lib/errors'
import { StatusBadge } from '../components/StatusBadge'
import { sessions, type SessionDetail as SessionDetailData } from '../lib/api'
import { formatRelative } from '../lib/format'

export function SessionDetail() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [dialog, setDialog] = useState<'rename' | 'delete' | null>(null)

  const q = useQuery({ queryKey: ['session', id], queryFn: () => sessions.get(id) })
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['session', id] })
    void queryClient.invalidateQueries({ queryKey: ['sessions'] })
  }
  const archive = useMutation({
    mutationFn: (archived: boolean) => sessions.patch(id, { archived }),
    onSuccess: invalidate,
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
          <button type="button" className="btn btn-danger" onClick={() => setDialog('delete')}>
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
          disabled
          title="PIN pairing arrives in milestone M2"
        >
          Add camera
        </button>
      </div>
      {s.cameras.length === 0 ? (
        <EmptyState message="No cameras yet — add one to mint a pairing PIN." />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Status</th>
                <th scope="col">Last seen</th>
                <th scope="col">Zones</th>
                <th scope="col">Device</th>
              </tr>
            </thead>
            <tbody>
              {s.cameras.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link to={`/cameras/${c.id}`}>{c.name}</Link>
                  </td>
                  <td>
                    <StatusBadge status={c.status} />
                  </td>
                  <td>{formatRelative(c.last_seen_at)}</td>
                  <td>
                    {c.zone_count}
                    {c.needs_review && (
                      <span className="warn" title="Frame size changed; verify the zone">
                        {' '}
                        <span aria-hidden="true">⚠</span> review
                      </span>
                    )}
                  </td>
                  <td className="muted">{c.device_label || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

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
            className="btn btn-danger"
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
