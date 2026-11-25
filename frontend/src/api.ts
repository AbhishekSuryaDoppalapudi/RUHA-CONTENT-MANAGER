// Typed client for the El Ruach Manager REST API (see /docs on the running server for the full schema).
export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers: body !== undefined && !(body instanceof FormData) ? { 'Content-Type': 'application/json' } : undefined,
      body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    })
  } catch {
    throw new ApiError('Cannot reach the El Ruach backend. Is it running?', 0)
  }
  if (!res.ok) {
    let msg = res.statusText
    try {
      const j = await res.json()
      msg = typeof j.detail === 'string' ? j.detail : Array.isArray(j.detail) ? j.detail.map((d: any) => d.msg).join('; ') : msg
    } catch { /* not JSON */ }
    throw new ApiError(msg, res.status)
  }
  if (res.status === 204) return undefined as T
  const ct = res.headers.get('content-type') || ''
  return (ct.includes('json') ? res.json() : res.text()) as Promise<T>
}

export const api = {
  get: <T,>(p: string) => request<T>('GET', p),
  post: <T,>(p: string, b?: unknown) => request<T>('POST', p, b ?? {}),
  put: <T,>(p: string, b?: unknown) => request<T>('PUT', p, b ?? {}),
  patch: <T,>(p: string, b?: unknown) => request<T>('PATCH', p, b ?? {}),
  del: <T,>(p: string) => request<T>('DELETE', p),
  /** multipart upload with progress (fetch cannot report upload progress) */
  upload<T>(path: string, form: FormData, onProgress?: (pct: number) => void): Promise<T> {
    return new Promise((resolve, reject) => {
      const x = new XMLHttpRequest()
      x.open('POST', path)
      x.upload.onprogress = (e) => e.lengthComputable && onProgress?.(Math.round((e.loaded / e.total) * 100))
      x.onerror = () => reject(new ApiError('Upload failed - cannot reach the backend', 0))
      x.onload = () => {
        let data: any = null
        try { data = JSON.parse(x.responseText) } catch { /* empty */ }
        if (x.status >= 200 && x.status < 300) resolve(data as T)
        else reject(new ApiError(typeof data?.detail === 'string' ? data.detail : `Upload failed (${x.status})`, x.status))
      }
      x.send(form)
    })
  },
}

export const qs = (o: Record<string, unknown>) => {
  const p = new URLSearchParams()
  Object.entries(o).forEach(([k, v]) => { if (v !== '' && v !== undefined && v !== null && v !== false) p.set(k, String(v)) })
  const s = p.toString()
  return s ? `?${s}` : ''
}

export const fileUrl = {
  asset: (id: number, download = false) => `/api/assets/${id}/file${download ? '?download=true' : ''}`,
  preview: (id: number, v?: number) => `/api/assets/${id}/preview${v ? `?v=${v}` : ''}`,
  video: (id: number) => `/api/videos/${id}/stream`,
  videoThumb: (id: number) => `/api/videos/${id}/thumbnail`,
  projectThumb: (id: number) => `/api/projects/${id}/thumbnail`,
  version: (id: number, v: number) => `/api/assets/${id}/versions/${v}/file`,
  variant: (id: number, fmt: string, v?: number, download = false) => `/api/assets/${id}/variants/${encodeURIComponent(fmt)}/file?${download ? 'download=true&' : ''}v=${v ?? 0}`,
}

// ------------------------------- types ------------------------------------
export type Video = {
  id: number; original_name: string; duration: number; width: number; height: number; fps: number
  size_bytes: number; video_codec: string | null; audio_tracks: number; audio_codec: string | null
  sha256: string; has_thumbnail: boolean; created_at: string; is_current: boolean
}
export type Project = {
  id: number; name: string; description: string; status: string; content_type: string; tags: string[]
  has_thumbnail: boolean; created_at: string; updated_at: string; video: Video | null
  asset_counts: Record<string, number>; has_transcript: boolean
  videos?: Video[]; jobs?: Job[]; translations?: Translation[]
}
export type Translation = { id: number; target: string; dubbed: boolean; status: string; video_asset_id: number | null; srt_asset_id: number | null; audio_asset_id: number | null }
export type Asset = {
  id: number; entity?: 'asset' | 'video'; project_id: number; project_name?: string; kind: string; slot: string | null
  title: string; description: string; caption: string; hashtags: string[]; mime: string | null; size_bytes: number
  status: string; version: number; meta: Record<string, any>; has_file: boolean; has_preview: boolean
  created_at: string; updated_at: string; tags?: string[]; duration?: number
  reel?: { category: string; start: number; end: number; hook: string; reason: string; excerpt: string; subtitle_asset_id: number | null }
  poster?: { headline: string; subheadline: string; quote: string; theme: string }
  social_post?: { headline: string; body: string; reference: string }
  last_review?: { action: string; actor: string; reason: string; at: string; version: number }
}
export type AssetDetail = Asset & {
  slide?: { reference: string; label: string }
  versions: { version: number; title: string; caption: string; description: string; hashtags: string[]; status: string; reason: string; created_by: string; created_at: string; has_file: boolean }[]
  reviews: { id: number; action: string; actor: string; reason: string; comment: string; version: number; at: string }[]
}
export type Job = {
  id: number; project_id: number | null; project_name?: string; job_type: string; status: string; progress: number; step: string
  error: string | null; attempts: number; created_at: string; started_at: string | null; completed_at: string | null
  params?: Record<string, any>; result?: Record<string, any>; logs?: { id: number; level: string; message: string; at: string }[]
}
export type CalEntry = {
  id: number; title: string; scheduled_for: string; status: string; asset_id: number | null; project_id: number | null
  campaign: string; category: string; platform: string; notes: string; reminder_at: string | null; reminder_done: boolean
  asset?: { id: number; kind: string; title: string; status: string }
}

export const LANGUAGES = ['Assamese', 'Bengali', 'Bodo', 'Dogri', 'English', 'Gujarati', 'Hindi', 'Kannada', 'Kashmiri', 'Konkani', 'Maithili',
  'Malayalam', 'Manipuri', 'Marathi', 'Nepali', 'Odia', 'Punjabi', 'Sanskrit', 'Santali', 'Sindhi', 'Tamil', 'Telugu', 'Urdu']
export const DUB_LANGUAGES = new Set(['Assamese', 'Bengali', 'Bodo', 'Dogri', 'English', 'Gujarati', 'Hindi', 'Kannada', 'Konkani', 'Maithili', 'Malayalam',
  'Manipuri', 'Marathi', 'Nepali', 'Odia', 'Punjabi', 'Sanskrit', 'Santali', 'Tamil', 'Telugu'])
export const PROJECT_STATUSES = ['draft', 'uploaded', 'processing', 'review_required', 'approved', 'published', 'failed', 'archived']
