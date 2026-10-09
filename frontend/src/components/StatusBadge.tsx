import type { CameraStatus } from '../lib/api'

const LABEL: Record<CameraStatus, string> = {
  paired: 'Paired, never connected',
  online: 'Online',
  offline: 'Offline',
  paused: 'Paused',
  error: 'Error',
  revoked: 'Revoked',
}

/** Status conveyed by text + colour dot, never colour alone (D. Accessibility). */
export function StatusBadge({ status }: { status: CameraStatus }) {
  return (
    <span className={`badge badge-${status}`} title={LABEL[status]}>
      {status}
    </span>
  )
}
