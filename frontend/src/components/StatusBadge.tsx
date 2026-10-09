import type { CameraStatus } from '../lib/api'

const GLYPH: Record<CameraStatus, string> = {
  paired: '◌',
  online: '●',
  offline: '○',
  paused: '❚❚',
  error: '✖',
  revoked: '⊘',
}

const LABEL: Record<CameraStatus, string> = {
  paired: 'Paired, never connected',
  online: 'Online',
  offline: 'Offline',
  paused: 'Paused',
  error: 'Error',
  revoked: 'Revoked',
}

/** Status conveyed by glyph + text + colour, never colour alone (D. Accessibility). */
export function StatusBadge({ status }: { status: CameraStatus }) {
  return (
    <span className={`badge badge-${status}`} title={LABEL[status]}>
      <span aria-hidden="true">{GLYPH[status]} </span>
      {status}
    </span>
  )
}
