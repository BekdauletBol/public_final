import { useEffect, useRef, useState } from 'react'
import { RULES, type ZoneEvent } from '../lib/api'
import { formatDateTime } from '../lib/format'

const ROW_HEIGHT = 40
const OVERSCAN = 8

const TYPE_LABEL: Record<string, string> = {
  zone_entered: 'entered',
  zone_cleared: 'cleared',
  occupancy_changed: 'count changed',
}

interface EventTableProps {
  events: ZoneEvent[]
  cameraNames?: Record<string, string>
  height?: number
  onEndReached?: () => void
}

/** Windowed table: only the visible rows are rendered (J.1 "virtualised"). */
export function EventTable({
  events,
  cameraNames = {},
  height = 480,
  onEndReached,
}: EventTableProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [open, setOpen] = useState<string | null>(null)

  useEffect(() => {
    const el = scrollRef.current
    if (!el || !onEndReached) return
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - ROW_HEIGHT * 2) onEndReached()
  }, [scrollTop, events.length, onEndReached])

  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN)
  const end = Math.min(events.length, Math.ceil((scrollTop + height) / ROW_HEIGHT) + OVERSCAN)
  const visible = events.slice(start, end)
  const selected = events.find((e) => e.id === open)

  return (
    <div className="event-table">
      <div className="event-head" role="row">
        <span>Time</span>
        <span>Camera</span>
        <span>Zone</span>
        <span>Event</span>
        <span>Count</span>
        <span>Rule</span>
      </div>
      <div
        ref={scrollRef}
        className="event-scroll"
        onScroll={(e) => setScrollTop((e.target as HTMLDivElement).scrollTop)}
        role="grid"
        aria-rowcount={events.length}
      >
        <div className="event-spacer" aria-hidden="true" data-rows={events.length} />
        <div className="event-rows" data-offset={start}>
          {visible.map((e) => (
            <button
              type="button"
              key={e.id}
              className={`event-row event-${e.type}${open === e.id ? ' selected' : ''}`}
              onClick={() => setOpen(open === e.id ? null : e.id)}
              role="row"
            >
              <span className="mono small">{formatDateTime(e.t_server)}</span>
              <span>{cameraNames[e.camera_id] ?? e.camera_id.slice(0, 8)}</span>
              <span>{e.zone_name ?? '—'}</span>
              <span>{TYPE_LABEL[e.type] ?? e.type}</span>
              <span>
                {e.count_before} → {e.count_after}
              </span>
              <span className="small muted">{e.active_rule}</span>
            </button>
          ))}
        </div>
      </div>
      {selected && (
        <div className="card event-detail">
          <h3>Per-rule counts for frame {selected.frame_seq}</h3>
          <dl className="kv">
            {RULES.map((r) => (
              <div key={r.id} className="kv-row">
                <dt>{r.label}</dt>
                <dd>
                  {selected.rule_counts[r.id] ?? '—'}
                  {r.id === selected.active_rule ? ' (active)' : ''}
                </dd>
              </div>
            ))}
          </dl>
          <p className="small muted mono">
            event {selected.id} · camera {selected.camera_id} · zone {selected.zone_id ?? '—'}
          </p>
        </div>
      )}
    </div>
  )
}
