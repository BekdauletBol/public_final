import { useMemo, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { EventTable } from '../components/EventTable'
import { EmptyState, ErrorState, Loading } from '../components/States'
import { cameras, events, sessions, type EventFilters, type EventType } from '../lib/api'

export function Events() {
  const [filters, setFilters] = useState<EventFilters>({})
  const sessionList = useQuery({ queryKey: ['sessions', true], queryFn: () => sessions.list(true) })
  const cameraList = useQuery({ queryKey: ['cameras'], queryFn: () => cameras.list() })
  const q = useInfiniteQuery({
    queryKey: ['events', filters],
    queryFn: ({ pageParam }) => events.list(filters, 200, pageParam ?? undefined),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  })
  const rows = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data])
  const cameraNames = useMemo(
    () => Object.fromEntries((cameraList.data ?? []).map((c) => [c.id, c.name])),
    [cameraList.data],
  )
  const set = (patch: Partial<EventFilters>) => setFilters((f) => ({ ...f, ...patch }))
  const toIso = (local: string) => (local ? new Date(local).toISOString() : undefined)

  return (
    <section>
      <div className="page-head">
        <h1>Events</h1>
        <a className="btn" href={events.exportUrl(filters)} download>
          Export CSV
        </a>
      </div>
      <form className="filter-bar" onSubmit={(e) => e.preventDefault()}>
        <label>
          Session
          <select
            value={filters.session_id ?? ''}
            onChange={(e) => set({ session_id: e.target.value || undefined })}
          >
            <option value="">All</option>
            {sessionList.data?.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Camera
          <select
            value={filters.camera_id ?? ''}
            onChange={(e) => set({ camera_id: e.target.value || undefined })}
          >
            <option value="">All</option>
            {cameraList.data
              ?.filter((c) => !filters.session_id || c.session_id === filters.session_id)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </select>
        </label>
        <label>
          Type
          <select
            value={filters.type ?? ''}
            onChange={(e) => set({ type: (e.target.value || '') as EventType | '' })}
          >
            <option value="">All</option>
            <option value="zone_entered">entered</option>
            <option value="zone_cleared">cleared</option>
            <option value="occupancy_changed">count changed</option>
          </select>
        </label>
        <label>
          From
          <input type="datetime-local" onChange={(e) => set({ from: toIso(e.target.value) })} />
        </label>
        <label>
          To
          <input type="datetime-local" onChange={(e) => set({ to: toIso(e.target.value) })} />
        </label>
      </form>
      {q.isPending && <Loading />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.isSuccess && rows.length === 0 && (
        <EmptyState message="No events match these filters. Draw a zone on a camera and events appear here as people enter or leave." />
      )}
      {q.isSuccess && rows.length > 0 && (
        <>
          <p className="small muted">
            {rows.length} events{q.hasNextPage ? ' (scroll for more)' : ''} · newest first · click a
            row for the per-rule counts
          </p>
          <EventTable
            events={rows}
            cameraNames={cameraNames}
            onEndReached={() => {
              if (q.hasNextPage && !q.isFetchingNextPage) void q.fetchNextPage()
            }}
          />
        </>
      )}
    </section>
  )
}
