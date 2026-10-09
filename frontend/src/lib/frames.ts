// Latest decoded frame and frame_result per camera, kept outside React state so that
// 10 fps previews never re-render component trees. Components subscribe per camera.

export interface DetectionMsg {
  bbox: [number, number, number, number]
  conf: number
  anchors: { bl: [number, number]; bc: [number, number]; br: [number, number] }
  track_id: number | null
}

export interface ZoneResultMsg {
  zone_id: string
  rule_hits: Record<string, number[]>
  counts: Record<string, number>
  active_rule: string
  state: 'clear' | 'occupied'
  debounced_count: number
}

export interface FrameResultMsg {
  type: 'frame_result'
  camera_id: string
  seq: number
  t_server_recv_ms: number
  t_processed_ms: number
  w: number
  h: number
  detections: DetectionMsg[]
  zones: ZoneResultMsg[]
  timings: Record<string, number>
}

export interface LiveFrame {
  blob: Blob
  seq: number
  receivedAt: number
}

type FrameListener = (frame: LiveFrame) => void
type ResultListener = (result: FrameResultMsg) => void

class FrameStore {
  private frames = new Map<string, LiveFrame>()
  private results = new Map<string, FrameResultMsg>()
  private frameListeners = new Map<string, Set<FrameListener>>()
  private resultListeners = new Map<string, Set<ResultListener>>()

  setFrame(cameraId: string, blob: Blob, seq: number): void {
    const frame = { blob, seq, receivedAt: performance.now() }
    this.frames.set(cameraId, frame)
    this.frameListeners.get(cameraId)?.forEach((l) => l(frame))
  }

  setResult(result: FrameResultMsg): void {
    this.results.set(result.camera_id, result)
    this.resultListeners.get(result.camera_id)?.forEach((l) => l(result))
  }

  latestFrame(cameraId: string): LiveFrame | undefined {
    return this.frames.get(cameraId)
  }

  latestResult(cameraId: string): FrameResultMsg | undefined {
    return this.results.get(cameraId)
  }

  onFrame(cameraId: string, listener: FrameListener): () => void {
    let set = this.frameListeners.get(cameraId)
    if (!set) {
      set = new Set()
      this.frameListeners.set(cameraId, set)
    }
    set.add(listener)
    return () => {
      set.delete(listener)
    }
  }

  onResult(cameraId: string, listener: ResultListener): () => void {
    let set = this.resultListeners.get(cameraId)
    if (!set) {
      set = new Set()
      this.resultListeners.set(cameraId, set)
    }
    set.add(listener)
    return () => {
      set.delete(listener)
    }
  }
}

export const frameStore = new FrameStore()
