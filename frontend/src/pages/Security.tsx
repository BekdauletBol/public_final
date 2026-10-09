import { useQuery } from '@tanstack/react-query'
import { EmptyState, ErrorState, Loading } from '../components/States'
import { security } from '../lib/api'
import { formatDateTime } from '../lib/format'

const LABEL: Record<string, string> = {
  login_failed: 'Failed login',
  login_ok: 'Login',
  redeem_failed: 'Failed pairing attempt',
  redeem_ok: 'Phone paired',
  pin_locked: 'PIN locked',
  token_revoked: 'Phone revoked',
  ws_auth_failed: 'Rejected phone connection',
  ws_superseded: 'Phone connection replaced',
}

export function Security() {
  const q = useQuery({
    queryKey: ['security-log'],
    queryFn: () => security.log(200),
    refetchInterval: 10_000,
  })

  return (
    <section>
      <div className="page-head">
        <div>
          <h1>Security</h1>
          <p className="muted small">
            Failed logins, failed pairing attempts and revocations. PINs and tokens are never
            recorded.
          </p>
        </div>
        <button type="button" className="btn" onClick={() => q.refetch()} disabled={q.isFetching}>
          Refresh
        </button>
      </div>
      {q.isPending && <Loading />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.isSuccess && q.data.length === 0 && <EmptyState message="Nothing recorded yet." />}
      {q.isSuccess && q.data.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Event</th>
                <th scope="col">IP</th>
                <th scope="col">Details</th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((e) => (
                <tr key={e.id}>
                  <td className="mono small">{formatDateTime(e.t)}</td>
                  <td>{LABEL[e.kind] ?? e.kind}</td>
                  <td className="mono small">{e.ip ?? '—'}</td>
                  <td className="small muted">
                    {e.details
                      ? Object.entries(e.details)
                          .map(([k, v]) => `${k}: ${String(v)}`)
                          .join(', ')
                      : ''}
                    {e.camera_id ? ` · camera ${e.camera_id.slice(0, 8)}` : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
