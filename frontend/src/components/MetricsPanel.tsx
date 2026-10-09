import type { CameraSummary } from '../lib/api'
import { useLive, type CameraMetrics, type Percentiles } from '../lib/ws'

function ms(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : `${Math.round(v)}`
}

function pct(p: Percentiles | undefined, key: keyof Percentiles): string {
  return ms(p?.[key])
}

function fps(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : v.toFixed(1)
}

export function MetricsPanel({ cameras }: { cameras: CameraSummary[] }) {
  const metrics = useLive((s) => s.metrics)
  const server = metrics?.server
  return (
    <section className="metrics">
      <div className="page-head">
        <h2>Metrics</h2>
        {server && (
          <span className="small muted">
            CPU {server.cpu_percent.toFixed(0)} % · {server.mem_mb.toFixed(0)} MB · device{' '}
            {server.device ?? 'none'} · {server.active_cameras} active · scheduler{' '}
            {server.scheduler_strategy}
            {server.eval_record ? ' · RECORDING' : ''}
          </span>
        )}
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Camera</th>
              <th scope="col">In fps</th>
              <th scope="col">Processed fps</th>
              <th scope="col">Dropped</th>
              <th scope="col">RTT ms</th>
              <th scope="col">Offset ms</th>
              <th scope="col">Infer p50/p95</th>
              <th scope="col">Total p50/p99</th>
              <th scope="col">Queue p50</th>
              <th scope="col">E2E p50</th>
            </tr>
          </thead>
          <tbody>
            {cameras.map((c) => {
              const m: CameraMetrics | undefined = metrics?.cameras[c.id]
              return (
                <tr key={c.id}>
                  <td>{c.name}</td>
                  <td>{fps(m?.fps_in)}</td>
                  <td>{fps(m?.fps_processed)}</td>
                  <td>{m ? `${(m.drop_ratio * 100).toFixed(1)} %` : '—'}</td>
                  <td>{ms(m?.rtt_ms)}</td>
                  <td>
                    {ms(m?.clock_offset_ms)}
                    {m?.clock_offset_uncertainty_ms != null
                      ? ` ±${Math.round(m.clock_offset_uncertainty_ms)}`
                      : ''}
                  </td>
                  <td>
                    {pct(m?.spans.infer, 'p50')}/{pct(m?.spans.infer, 'p95')}
                  </td>
                  <td>
                    {pct(m?.spans.total, 'p50')}/{pct(m?.spans.total, 'p99')}
                  </td>
                  <td>{pct(m?.spans.queue_wait, 'p50')}</td>
                  <td>{pct(m?.spans.e2e_estimate, 'p50')}</td>
                </tr>
              )
            })}
            {cameras.length === 0 && (
              <tr>
                <td colSpan={10} className="muted">
                  No cameras in this session.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
