import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { Zone } from '../lib/api'
import { frameStore, type FrameResultMsg } from '../lib/frames'
import { drawOverlay } from '../lib/overlay'

interface LiveViewProps {
  cameraId: string
  maxFps?: number
  className?: string
  label?: string
  zones?: Zone[]
  overlays?: boolean
  children?: ReactNode
  onFrameSize?: (w: number, h: number) => void
}

/** JPEG preview on one canvas, overlays (boxes, anchors, zones) drawn on a second canvas. */
export function LiveView({
  cameraId,
  maxFps = 10,
  className,
  label,
  zones = [],
  overlays = true,
  children,
  onFrameSize,
}: LiveViewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const overlayRef = useRef<HTMLCanvasElement>(null)
  const [hasFrame, setHasFrame] = useState(false)
  const [stale, setStale] = useState(false)
  const zonesRef = useRef(zones)
  const redrawRef = useRef<() => void>(() => undefined)

  useEffect(() => {
    zonesRef.current = zones
    redrawRef.current()
  }, [zones])

  useEffect(() => {
    let lastDraw = 0
    let drawing = false
    let lastSeen = 0
    let size: [number, number] | null = null
    const redrawOverlay = (result?: FrameResultMsg) => {
      const overlay = overlayRef.current
      if (!overlay || !size) return
      const ctx = overlay.getContext('2d')
      if (!ctx) return
      drawOverlay(ctx, size[0], size[1], result, overlays ? zonesRef.current : [])
    }
    redrawRef.current = () => redrawOverlay(frameStore.latestResult(cameraId))
    const draw = async (blob: Blob) => {
      const canvas = canvasRef.current
      const overlay = overlayRef.current
      if (!canvas || !overlay || drawing) return
      drawing = true
      try {
        const bitmap = await createImageBitmap(blob)
        if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
          canvas.width = bitmap.width
          canvas.height = bitmap.height
          overlay.width = bitmap.width
          overlay.height = bitmap.height
          size = [bitmap.width, bitmap.height]
          onFrameSize?.(bitmap.width, bitmap.height)
        }
        canvas.getContext('2d')?.drawImage(bitmap, 0, 0)
        bitmap.close()
        lastSeen = performance.now()
        setHasFrame(true)
        setStale(false)
        redrawOverlay(frameStore.latestResult(cameraId))
      } catch {
        /* undecodable preview */
      } finally {
        drawing = false
      }
    }
    const offFrame = frameStore.onFrame(cameraId, (frame) => {
      const now = performance.now()
      if (now - lastDraw < 1000 / maxFps) return
      lastDraw = now
      void draw(frame.blob)
    })
    const offResult = frameStore.onResult(cameraId, (result) => {
      if (!size) {
        size = [result.w, result.h]
        const overlay = overlayRef.current
        if (overlay) {
          overlay.width = result.w
          overlay.height = result.h
        }
      }
      redrawOverlay(result)
    })
    const existing = frameStore.latestFrame(cameraId)
    if (existing) void draw(existing.blob)
    const staleTimer = window.setInterval(() => {
      if (lastSeen && performance.now() - lastSeen > 3000) setStale(true)
    }, 1000)
    return () => {
      offFrame()
      offResult()
      window.clearInterval(staleTimer)
    }
  }, [cameraId, maxFps, overlays, onFrameSize])

  return (
    <div className={className ? `live ${className}` : 'live'}>
      <canvas ref={canvasRef} className="live-canvas" aria-label={label ?? 'Live camera view'} />
      <canvas ref={overlayRef} className="live-canvas live-overlay-canvas" aria-hidden="true" />
      {!hasFrame && <div className="live-overlay">No frames yet</div>}
      {hasFrame && stale && <div className="live-overlay">Frames paused</div>}
      {children}
    </div>
  )
}
