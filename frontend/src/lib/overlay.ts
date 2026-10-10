// Client-side overlay drawing (F-CV-5): boxes with confidence, the three foot anchors,
// zone polygons (green clear / red occupied / grey disabled) and per-zone counts.
import type { Zone } from './api'
import type { FrameResultMsg } from './frames'

export interface OverlayColors {
  box: string
  text: string
  clear: string
  occupied: string
  disabled: string
}

export const DEFAULT_COLORS: OverlayColors = {
  box: '#ffffff',
  text: '#ffffff',
  clear: '#22c55e',
  occupied: '#ef4444',
  disabled: '#9ca3af',
}

export function drawOverlay(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  result: FrameResultMsg | undefined,
  zones: Zone[],
  colors: OverlayColors = DEFAULT_COLORS,
): void {
  ctx.clearRect(0, 0, width, height)
  // Detections are in the decoded frame's pixel space (result.w x result.h).
  const sx = result ? width / result.w : 1
  const sy = result ? height / result.h : 1
  const line = Math.max(1, Math.round(width / 480))
  const font = `${Math.max(11, Math.round(width / 60))}px -apple-system, system-ui, sans-serif`
  ctx.font = font
  ctx.lineJoin = 'round'

  const stateOf = new Map<string, { state: string; count: number; enabled: boolean }>()
  result?.zones.forEach((z) =>
    stateOf.set(z.zone_id, {
      state: z.state,
      count: z.counts[z.active_rule] ?? 0,
      enabled: (z as { enabled?: boolean }).enabled ?? true,
    }),
  )

  for (const zone of zones) {
    const live = stateOf.get(zone.id)
    const enabled = live ? live.enabled : zone.enabled
    const color = !enabled
      ? colors.disabled
      : live?.state === 'occupied'
        ? colors.occupied
        : colors.clear
    ctx.beginPath()
    zone.points.forEach(([x, y], i) => {
      const px = x * width
      const py = y * height
      if (i === 0) ctx.moveTo(px, py)
      else ctx.lineTo(px, py)
    })
    ctx.closePath()
    ctx.fillStyle = color + '33'
    ctx.fill()
    ctx.strokeStyle = color
    ctx.lineWidth = line * 2
    ctx.setLineDash(enabled ? [] : [8, 6])
    ctx.stroke()
    ctx.setLineDash([])
    const [lx, ly] = zone.points[0]
    const label = `${zone.name}${live ? ` · ${live.count}` : ''}${enabled ? '' : ' (off)'}`
    drawLabel(ctx, label, lx * width, ly * height, color)
  }

  if (!result) return
  ctx.lineWidth = line
  for (const det of result.detections) {
    const [x1, y1, x2, y2] = det.bbox
    ctx.strokeStyle = colors.box
    ctx.strokeRect(x1 * sx, y1 * sy, (x2 - x1) * sx, (y2 - y1) * sy)
    drawLabel(ctx, det.conf.toFixed(2), x1 * sx, y1 * sy, colors.box)
    for (const key of ['bl', 'bc', 'br'] as const) {
      const [ax, ay] = det.anchors[key]
      ctx.beginPath()
      ctx.arc(ax * sx, ay * sy, line * 2.5, 0, Math.PI * 2)
      ctx.fillStyle = key === 'bc' ? colors.occupied : colors.box
      ctx.fill()
    }
  }
}

function drawLabel(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  color: string,
) {
  const pad = 3
  const metrics = ctx.measureText(text)
  const h = parseInt(ctx.font, 10) + pad * 2
  const top = Math.max(0, y - h)
  ctx.fillStyle = 'rgba(0,0,0,0.6)'
  ctx.fillRect(x, top, metrics.width + pad * 2, h)
  ctx.fillStyle = color
  ctx.fillText(text, x + pad, top + h - pad - 2)
}
