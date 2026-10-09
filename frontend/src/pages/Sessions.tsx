import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Dialog } from '../components/Dialog'
import { EmptyState, ErrorState, Loading } from '../components/States'
import { errorMessage } from '../lib/errors'
import { sessions } from '../lib/api'
import { formatDateTime } from '../lib/format'

export function Sessions() {
  const [includeArchived, setIncludeArchived] = useState(false)
  const [dialogOpen, setDialogOpen] = useState(false)
  const queryClient = useQueryClient()
  const list = useQuery({
    queryKey: ['sessions', includeArchived],
    queryFn: () => sessions.list(includeArchived),
  })

  const newButton = (
    <button type="button" className="btn btn-primary" onClick={() => setDialogOpen(true)}>
      New session
    </button>
  )

  return (
    <section>
      <div className="page-head">
        <h1>Sessions</h1>
        <div className="page-actions">
          <label className="checkbox">
            <input
              type="checkbox"
              checked={includeArchived}
              onChange={(e) => setIncludeArchived(e.target.checked)}
            />
            Show archived
          </label>
          {newButton}
        </div>
      </div>

      {list.isPending && <Loading />}
      {list.isError && <ErrorState error={list.error} onRetry={() => list.refetch()} />}
      {list.isSuccess && list.data.length === 0 && (
        <EmptyState
          message="No sessions yet — create one to start pairing phones."
          action={newButton}
        />
      )}
      {list.isSuccess && list.data.length > 0 && (
        <ul className="card-list">
          {list.data.map((s) => (
            <li key={s.id} className={s.archived_at ? 'card card-row archived' : 'card card-row'}>
              <div>
                <Link to={`/sessions/${s.id}`} className="card-title">
                  {s.name}
                </Link>
                <div className="muted">
                  {s.location_note ? `${s.location_note} · ` : ''}
                  created {formatDateTime(s.created_at)}
                  {s.archived_at ? ' · archived' : ''}
                </div>
              </div>
              <div className="card-meta" title="online cameras / all cameras">
                <span aria-hidden="true">📷 </span>
                {s.online_count}/{s.camera_count} online
              </div>
            </li>
          ))}
        </ul>
      )}

      <NewSessionDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onCreated={() => {
          setDialogOpen(false)
          void queryClient.invalidateQueries({ queryKey: ['sessions'] })
        }}
      />
    </section>
  )
}

function NewSessionDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  onCreated: () => void
}) {
  const [name, setName] = useState('')
  const [note, setNote] = useState('')
  const create = useMutation({
    mutationFn: () => sessions.create({ name, location_note: note || undefined }),
    onSuccess: () => {
      setName('')
      setNote('')
      onCreated()
    },
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    create.mutate()
  }
  return (
    <Dialog open={open} title="New session" onClose={onClose}>
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
          Location note (optional)
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={200} />
        </label>
        {create.isError && (
          <p className="form-error" role="alert">
            {errorMessage(create.error)}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={create.isPending}>
            Create
          </button>
        </div>
      </form>
    </Dialog>
  )
}
