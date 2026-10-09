import { useQuery } from '@tanstack/react-query'
import { ErrorState, Loading } from '../components/States'
import { health } from '../lib/api'
import { useLive } from '../lib/ws'

export function Health() {
  const q = useQuery({ queryKey: ['health'], queryFn: health.get, refetchInterval: 5000 })
  const metrics = useLive((s) => s.metrics)
  const server = metrics?.server
  return (
    <section>
      <div className="page-head">
        <h1>Health</h1>
        {server?.eval_record && <span className="badge badge-error">RECORDING</span>}
      </div>
      {q.isPending && <Loading />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.isSuccess && (
        <div className="card">
          <dl className="kv">
            <dt>Server version</dt>
            <dd>{q.data.version}</dd>
            <dt>Uptime</dt>
            <dd>{Math.round(q.data.uptime_s)} s</dd>
            <dt>Inference device</dt>
            <dd>{q.data.device ?? 'none (pass-through until M4)'}</dd>
            <dt>Model SHA-256</dt>
            <dd className="mono small">{q.data.model_sha256 ?? '—'}</dd>
            <dt>Database</dt>
            <dd>{q.data.db ?? '—'}</dd>
            <dt>Scheduler</dt>
            <dd>
              {server?.scheduler_strategy ?? '—'}
              {server ? ` (backlog ${server.scheduler_backlog})` : ''}
            </dd>
            <dt>Recording</dt>
            <dd>{server?.eval_record ? 'EVAL_RECORD=1: frames are written to disk' : 'off'}</dd>
            <dt>CPU / memory</dt>
            <dd>
              {server ? `${server.cpu_percent.toFixed(0)} % · ${server.mem_mb.toFixed(0)} MB` : '—'}
            </dd>
            <dt>Active cameras / dashboards</dt>
            <dd>{server ? `${server.active_cameras} / ${server.dashboards}` : '—'}</dd>
          </dl>
        </div>
      )}
    </section>
  )
}
