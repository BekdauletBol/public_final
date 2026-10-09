// Typed HTTP client for the public.safe API (spec Section F).
// Every non-GET request carries the CSRF header; the admin cookie travels automatically.

export class ApiError extends Error {
  status: number
  code: string
  details: Record<string, unknown>
  retryAfterS: number | null

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> = {},
    retryAfterS: number | null = null,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
    this.retryAfterS = retryAfterS
  }
}

export const UNAUTHENTICATED_EVENT = 'publicsafe:unauthenticated'

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE'

async function request<T>(method: Method, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (method !== 'GET') headers['X-Requested-With'] = 'publicsafe'
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  let response: Response
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (e) {
    throw new ApiError(0, 'network_error', e instanceof Error ? e.message : 'Network error')
  }

  if (response.status === 204) return undefined as T
  const text = await response.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = null
    }
  }
  if (!response.ok) {
    const err = (
      data as { error?: { code?: string; message?: string; details?: Record<string, unknown> } }
    )?.error
    const retry = response.headers.get('Retry-After')
    const apiError = new ApiError(
      response.status,
      err?.code ?? `http_${response.status}`,
      err?.message ?? response.statusText,
      err?.details ?? {},
      retry ? Number(retry) : null,
    )
    if (response.status === 401 && !path.startsWith('/api/auth/login')) {
      window.dispatchEvent(new CustomEvent(UNAUTHENTICATED_EVENT))
    }
    throw apiError
  }
  return data as T
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body),
  delete: <T>(path: string) => request<T>('DELETE', path),
}

// --- types (mirror backend/publicsafe/api/schemas.py) -----------------------

export type CameraStatus = 'paired' | 'online' | 'offline' | 'paused' | 'error' | 'revoked'

export interface Me {
  username: string
  session_expires_at: string
}

export interface Session {
  id: string
  name: string
  location_note: string | null
  created_at: string
  archived_at: string | null
  camera_count: number
  online_count: number
}

export interface CameraSummary {
  id: string
  session_id: string
  name: string
  status: CameraStatus
  device_label: string | null
  created_at: string
  last_seen_at: string | null
  fps_in: number | null
  fps_processed: number | null
  drop_ratio: number | null
  rtt_ms: number | null
  clock_offset_ms: number | null
  occupancy: Record<string, number>
  zone_count: number
  needs_review: boolean
}

export interface SessionDetail extends Session {
  cameras: CameraSummary[]
}

export interface CameraSettings {
  target_fps: number
  jpeg_quality: number
  max_long_edge: number
  confidence: number
  privacy_mode: boolean
}

export interface Zone {
  id: string
  camera_id: string
  name: string
  points: [number, number][]
  frame_w: number
  frame_h: number
  active_rule: string
  enter_frames: number
  exit_frames: number
  enabled: boolean
  needs_review: boolean
  created_at: string
  updated_at: string
}

export interface CameraDetail extends CameraSummary {
  settings: CameraSettings
  zones: Zone[]
  health: Record<string, unknown>
}

export interface SecurityLogEntry {
  id: string
  t: string
  kind: string
  ip: string | null
  camera_id: string | null
  session_id: string | null
  details: Record<string, unknown> | null
}

export interface Health {
  status: string
  version: string
  uptime_s: number
  device: string | null
  model_sha256: string | null
  db: string | null
}

// --- endpoint helpers --------------------------------------------------------

export const auth = {
  login: (username: string, password: string) =>
    api.post<void>('/api/auth/login', { username, password }),
  logout: () => api.post<void>('/api/auth/logout'),
  me: () => api.get<Me>('/api/auth/me'),
}

export const sessions = {
  list: (includeArchived: boolean) =>
    api.get<Session[]>(`/api/sessions?include_archived=${includeArchived}`),
  get: (id: string) => api.get<SessionDetail>(`/api/sessions/${id}`),
  create: (body: { name: string; location_note?: string }) =>
    api.post<Session>('/api/sessions', body),
  patch: (id: string, body: { name?: string; location_note?: string | null; archived?: boolean }) =>
    api.patch<Session>(`/api/sessions/${id}`, body),
  remove: (id: string) => api.delete<void>(`/api/sessions/${id}`),
}

export const cameras = {
  list: (sessionId?: string) =>
    api.get<CameraSummary[]>(sessionId ? `/api/cameras?session_id=${sessionId}` : '/api/cameras'),
  get: (id: string) => api.get<CameraDetail>(`/api/cameras/${id}`),
  patch: (id: string, body: { name?: string; settings?: Partial<CameraSettings> }) =>
    api.patch<CameraDetail>(`/api/cameras/${id}`, body),
  revoke: (id: string, reason?: string) =>
    api.post<void>(`/api/cameras/${id}/revoke`, reason ? { reason } : {}),
  remove: (id: string) => api.delete<void>(`/api/cameras/${id}`),
}

export interface PairingCodeMinted {
  code_id: string
  pin: string
  expires_at: string
  pair_url: string
  ttl_seconds: number
}

export type PairingCodeStatus = 'pending' | 'redeemed' | 'expired' | 'locked' | 'cancelled'

export interface PairingCode {
  code_id: string
  status: PairingCodeStatus
  created_at: string
  expires_at: string
  attempts: number
  max_attempts: number
  redeemed_camera_id: string | null
  replace_camera_id: string | null
  camera_name_hint: string | null
}

export const pairing = {
  mint: (sessionId: string, body: { camera_name_hint?: string; replace_camera_id?: string }) =>
    api.post<PairingCodeMinted>(`/api/sessions/${sessionId}/pairing-codes`, body),
  list: (sessionId: string) => api.get<PairingCode[]>(`/api/sessions/${sessionId}/pairing-codes`),
  cancel: (codeId: string) => api.delete<void>(`/api/pairing-codes/${codeId}`),
}

export const security = {
  log: (limit = 200) => api.get<SecurityLogEntry[]>(`/api/security/log?limit=${limit}`),
}

export const health = {
  get: () => api.get<Health>('/api/health'),
}
