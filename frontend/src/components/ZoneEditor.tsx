import { useCallback, useEffect, useRef, useState, type FormEvent, type PointerEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { RULES, zones as zonesApi, type Zone, type ZoneInput } from '../lib/api'
import { errorMessage } from '../lib/errors'

type Pt = [number, number]

interface ZoneEditorProps {
  cameraId: string
  frameSize: [number, number]
  existing: Zone | null
  onDone: () => void
}

const MIN_POINTS = 3
const MAX_POINTS = 20

function clamp01(v: number): number {
  return Math.min(1, Math.max(0, v))
}

function segmentsCross(a: Pt, b: Pt, c: Pt, d: Pt): boolean {
  const o = (p: Pt, q: Pt, r: Pt) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
  const o1 = o(a, b, c)
  const o2 = o(a, b, d)
  const o3 = o(c, d, a)
  const o4 = o(c, d, b)
  return o1 * o2 < 0 && o3 * o4 < 0
}

function selfIntersects(points: Pt[]): boolean {
  const n = points.length
  if (n < 4) return false
  for (let i = 0; i < n; i++) {
    for (let j = i + 2; j < n; j++) {
      if (i === 0 && j === n - 1) continue
      if (segmentsCross(points[i], points[(i + 1) % n], points[j], points[(j + 1) % n])) return true
    }
  }
  return false
}

/**
 * Draws a polygon over the frame in normalised coordinates (F-ZONE-1/2, J.3):
 * click adds a vertex, drag moves one, double-click/Enter closes, Esc cancels,
 * Delete removes the selected vertex, Cmd/Ctrl-Z undoes the last vertex.
 */
export function ZoneEditor({ cameraId, frameSize, existing, onDone }: ZoneEditorProps) {
  const queryClient = useQueryClient()
  const svgRef = useRef<SVGSVGElement>(null)
  const [points, setPoints] = useState<Pt[]>(existing ? existing.points : [])
  const [closed, setClosed] = useState(existing !== null)
  const [selected, setSelected] = useState<number | null>(null)
  const [dragging, setDragging] = useState<number | null>(null)
  const [name, setName] = useState(existing?.name ?? 'Zone')
  const [rule, setRule] = useState(existing?.active_rule ?? 'bottom_center')
  const [enterFrames, setEnterFrames] = useState(existing?.enter_frames ?? 3)
  const [exitFrames, setExitFrames] = useState(existing?.exit_frames ?? 5)
  const [enabled, setEnabled] = useState(existing?.enabled ?? true)
  const [W, H] = frameSize

  const save = useMutation({
    mutationFn: (body: ZoneInput) =>
      existing ? zonesApi.patch(existing.id, body) : zonesApi.create(cameraId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['camera', cameraId] })
      onDone()
    },
  })

  const toNorm = useCallback((e: PointerEvent<Element>): Pt => {
    const svg = svgRef.current!
    const rect = svg.getBoundingClientRect()
    return [
      clamp01((e.clientX - rect.left) / rect.width),
      clamp01((e.clientY - rect.top) / rect.height),
    ]
  }, [])

  const onSurfacePointerDown = (e: PointerEvent<SVGRectElement>) => {
    if (closed || points.length >= MAX_POINTS) return
    const p = toNorm(e)
    setPoints((pts) => [...pts, p])
    setSelected(points.length)
  }

  const onVertexPointerDown = (index: number, e: PointerEvent<SVGCircleElement>) => {
    e.stopPropagation()
    setSelected(index)
    setDragging(index)
    ;(e.target as Element).setPointerCapture(e.pointerId)
  }

  const onPointerMove = (e: PointerEvent<SVGSVGElement>) => {
    if (dragging === null) return
    const p = toNorm(e)
    setPoints((pts) => pts.map((q, i) => (i === dragging ? p : q)))
  }

  const onPointerUp = () => setDragging(null)

  const close = useCallback(() => {
    if (points.length >= MIN_POINTS) setClosed(true)
  }, [points.length])

  /** Double-click closes; its first click already added a vertex on top of the previous one. */
  const closeByDoubleClick = useCallback(() => {
    setPoints((pts) => {
      if (pts.length < 2) return pts
      const [ax, ay] = pts[pts.length - 2]
      const [bx, by] = pts[pts.length - 1]
      const duplicate = Math.hypot(ax - bx, ay - by) < 0.01
      const kept = duplicate ? pts.slice(0, -1) : pts
      if (kept.length >= MIN_POINTS) setClosed(true)
      return kept
    })
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement | null)?.tagName === 'INPUT') return
      if (e.key === 'Escape') onDone()
      else if (e.key === 'Enter') close()
      else if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selected !== null) {
          setPoints((pts) => pts.filter((_, i) => i !== selected))
          setSelected(null)
          setClosed((c) => c && points.length - 1 >= MIN_POINTS)
        }
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        setPoints((pts) => pts.slice(0, -1))
        setClosed(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close, onDone, points.length, selected])

  const crossing = selfIntersects(points)
  const problems: string[] = []
  if (points.length < MIN_POINTS)
    problems.push(`Add at least ${MIN_POINTS} points (${points.length} so far).`)
  if (crossing) problems.push('The polygon edges cross each other.')
  if (!closed && points.length >= MIN_POINTS)
    problems.push('Double-click or press Enter to close the polygon.')
  const canSave = problems.length === 0 && name.trim().length > 0

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!canSave) return
    save.mutate({
      name: name.trim(),
      points: points.map(([x, y]) => [Number(x.toFixed(4)), Number(y.toFixed(4))]),
      frame_w: W,
      frame_h: H,
      active_rule: rule,
      enter_frames: enterFrames,
      exit_frames: exitFrames,
      enabled,
    })
  }

  const path = points.map(([x, y]) => `${x * W},${y * H}`).join(' ')
  const r = Math.max(4, W / 160)

  return (
    <div className="zone-editor">
      <svg
        ref={svgRef}
        className="zone-surface"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="application"
        aria-label="Zone editor: click to add points, drag to move, double-click to close"
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onDoubleClick={closeByDoubleClick}
      >
        <rect
          x={0}
          y={0}
          width={W}
          height={H}
          fill="transparent"
          onPointerDown={onSurfacePointerDown}
        />
        {points.length > 1 &&
          (closed ? (
            <polygon
              points={path}
              className={crossing ? 'zone-shape zone-shape-bad' : 'zone-shape'}
            />
          ) : (
            <polyline points={path} className="zone-shape zone-shape-open" />
          ))}
        {points.map(([x, y], i) => (
          <circle
            key={i}
            cx={x * W}
            cy={y * H}
            r={r}
            className={i === selected ? 'zone-vertex zone-vertex-selected' : 'zone-vertex'}
            onPointerDown={(e) => onVertexPointerDown(i, e)}
            aria-label={`Vertex ${i + 1}`}
          />
        ))}
      </svg>
      <form className="form zone-form" onSubmit={submit}>
        <div className="small muted">
          {points.length} / {MAX_POINTS} points · {closed ? 'closed' : 'open'} · Esc cancels, ⌘Z
          undoes
        </div>
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required />
        </label>
        <label>
          Active rule
          <select value={rule} onChange={(e) => setRule(e.target.value)}>
            {RULES.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label} — {r.description}
              </option>
            ))}
          </select>
        </label>
        <div className="form-row">
          <label>
            Enter after (frames)
            <input
              type="number"
              min={1}
              max={60}
              value={enterFrames}
              onChange={(e) => setEnterFrames(Number(e.target.value))}
            />
          </label>
          <label>
            Clear after (frames)
            <input
              type="number"
              min={1}
              max={60}
              value={exitFrames}
              onChange={(e) => setExitFrames(Number(e.target.value))}
            />
          </label>
        </div>
        <label className="checkbox">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Enabled
        </label>
        <p className="form-error" role="alert">
          {problems[0] ?? (save.isError ? errorMessage(save.error) : '')}
        </p>
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onDone}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={!canSave || save.isPending}>
            {existing ? 'Save zone' : 'Create zone'}
          </button>
        </div>
      </form>
    </div>
  )
}
