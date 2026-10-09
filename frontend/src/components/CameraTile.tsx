import { Link } from 'react-router-dom'
import type { CameraSummary } from '../lib/api'
import { formatRelative } from '../lib/format'
import { useLive } from '../lib/ws'
import { StatusBadge } from './StatusBadge'

interface CameraTileProps {
  camera: CameraSummary
  onReplace: (camera: CameraSummary) => void
  onRevoke: (camera: CameraSummary) => void
}

/** Camera grid tile: the frame area is the hero; live status comes from the socket. */
export function CameraTile({ camera, onReplace, onRevoke }: CameraTileProps) {
  const live = useLive((s) => s.statuses[camera.id])
  const status = live?.status ?? camera.status
  const lastSeen = live?.last_seen_at ?? camera.last_seen_at
  const revoked = status === 'revoked'

  return (
    <article className="tile" aria-label={`Camera ${camera.name}`}>
      <Link to={`/cameras/${camera.id}`} className="tile-frame" aria-label={`Open ${camera.name}`}>
        {status === 'online' ? 'Live preview arrives in M3' : 'No frames'}
      </Link>
      <div className="tile-body">
        <div className="tile-head">
          <Link to={`/cameras/${camera.id}`} className="card-title">
            {camera.name}
          </Link>
          <StatusBadge status={status} />
        </div>
        <div className="small muted">
          Last seen {formatRelative(lastSeen)} · {camera.zone_count}{' '}
          {camera.zone_count === 1 ? 'zone' : 'zones'}
          {camera.needs_review && (
            <>
              {' '}
              · <span className="warn">⚠ review zone</span>
            </>
          )}
          {camera.device_label && <> · {camera.device_label}</>}
        </div>
        <div className="tile-actions">
          <button type="button" className="btn btn-sm" onClick={() => onReplace(camera)}>
            Replace phone
          </button>
          {!revoked && (
            <button type="button" className="btn btn-sm btn-quiet" onClick={() => onRevoke(camera)}>
              Revoke
            </button>
          )}
        </div>
      </div>
    </article>
  )
}
