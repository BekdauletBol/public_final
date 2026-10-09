import { useEffect, useRef, useState } from 'react'
import { frameStore } from '../lib/frames'

interface LiveViewProps {
  cameraId: string
  maxFps?: number
  className?: string
  label?: string
}

/** Draws the latest JPEG preview on a canvas; overlays (boxes, zones) are added in M4. */
export function LiveView({ cameraId, maxFps = 10, className, label }: LiveViewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [hasFrame, setHasFrame] = useState(false)
  const [stale, setStale] = useState(false)

  useEffect(() => {
    let lastDraw = 0
    let drawing = false
    let lastSeen = 0
    const draw = async (blob: Blob) => {
      const canvas = canvasRef.current
      if (!canvas || drawing) return
      drawing = true
      try {
        const bitmap = await createImageBitmap(blob)
        if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
          canvas.width = bitmap.width
          canvas.height = bitmap.height
        }
        canvas.getContext('2d')?.drawImage(bitmap, 0, 0)
        bitmap.close()
        lastSeen = performance.now()
        setHasFrame(true)
        setStale(false)
      } catch {
        /* undecodable preview */
      } finally {
        drawing = false
      }
    }
    const off = frameStore.onFrame(cameraId, (frame) => {
      const now = performance.now()
      if (now - lastDraw < 1000 / maxFps) return
      lastDraw = now
      void draw(frame.blob)
    })
    const existing = frameStore.latestFrame(cameraId)
    if (existing) void draw(existing.blob)
    const staleTimer = window.setInterval(() => {
      if (lastSeen && performance.now() - lastSeen > 3000) setStale(true)
    }, 1000)
    return () => {
      off()
      window.clearInterval(staleTimer)
    }
  }, [cameraId, maxFps])

  return (
    <div className={className ? `live ${className}` : 'live'}>
      <canvas ref={canvasRef} className="live-canvas" aria-label={label ?? 'Live camera view'} />
      {!hasFrame && <div className="live-overlay">No frames yet</div>}
      {hasFrame && stale && <div className="live-overlay">Frames paused</div>}
    </div>
  )
}
