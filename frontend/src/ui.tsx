import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Info, Loader2, X, XCircle } from 'lucide-react'
import { ApiError } from './api'

// ------------------------------ formatting -------------------------------
export const fmtBytes = (n: number) => {
  if (!n) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)))
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`
}
export const fmtDur = (s: number) => {
  s = Math.round(s || 0)
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}` : `${m}:${String(r).padStart(2, '0')}`
}
export const fmtDate = (iso?: string | null) => {
  if (!iso) return '-'
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}
export const ago = (iso?: string | null) => {
  if (!iso) return ''
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z').getTime()
  const s = Math.max(0, (Date.now() - d) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}
export const label = (s: string) => s.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
export const PROJECT_STATUS_OPTIONS = ['draft', 'uploaded', 'processing', 'review_required', 'approved', 'published', 'failed', 'archived']

// ------------------------------ toasts -----------------------------------
type Toast = { id: number; kind: 'ok' | 'err' | 'info'; text: string }
const ToastCtx = createContext<{ ok: (t: string) => void; err: (e: unknown) => void; info: (t: string) => void }>(null as any)
export const useToast = () => useContext(ToastCtx)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([])
  const push = useCallback((kind: Toast['kind'], text: string) => {
    const id = Date.now() + Math.random()
    setItems((x) => [...x, { id, kind, text }])
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), kind === 'err' ? 7000 : 3500)
  }, [])
  const api = {
    ok: (t: string) => push('ok', t), info: (t: string) => push('info', t),
    err: (e: unknown) => push('err', e instanceof Error ? e.message : String(e)),
  }
  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 w-[min(92vw,380px)]" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className="card animate-fadeUp flex items-start gap-3 p-3 shadow-xl">
            {t.kind === 'ok' ? <CheckCircle2 className="text-ok shrink-0" size={18} /> : t.kind === 'err' ? <XCircle className="text-danger shrink-0" size={18} /> : <Info className="text-accent shrink-0" size={18} />}
            <p className="text-sm flex-1">{t.text}</p>
            <button className="text-muted hover:text-ink" onClick={() => setItems((x) => x.filter((y) => y.id !== t.id))} aria-label="Dismiss"><X size={14} /></button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

// ----------------------------- confirm dialog ----------------------------
type ConfirmOpts = { title: string; body?: ReactNode; confirm?: string; danger?: boolean; requireText?: string }
const ConfirmCtx = createContext<(o: ConfirmOpts) => Promise<boolean>>(null as any)
export const useConfirm = () => useContext(ConfirmCtx)

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<(ConfirmOpts & { resolve: (v: boolean) => void }) | null>(null)
  const [typed, setTyped] = useState('')
  const ask = useCallback((o: ConfirmOpts) => new Promise<boolean>((resolve) => { setTyped(''); setState({ ...o, resolve }) }), [])
  const close = (v: boolean) => { state?.resolve(v); setState(null) }
  return (
    <ConfirmCtx.Provider value={ask}>
      {children}
      {state && (
        <Modal title={state.title} onClose={() => close(false)} width="max-w-md">
          {state.body && <div className="text-sm text-muted mb-4">{state.body}</div>}
          {state.requireText && (
            <div className="mb-4">
              <label className="label">Type <b className="text-ink">{state.requireText}</b> to confirm</label>
              <input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus />
            </div>
          )}
          <div className="flex justify-end gap-2">
            <button className="btn-ghost" onClick={() => close(false)}>Cancel</button>
            <button className={state.danger ? 'btn-danger' : 'btn-primary'} disabled={!!state.requireText && typed !== state.requireText}
              onClick={() => close(true)}>{state.confirm || 'Confirm'}</button>
          </div>
        </Modal>
      )}
    </ConfirmCtx.Provider>
  )
}

// ------------------------------- primitives ------------------------------
export function Modal({ title, children, onClose, width = 'max-w-2xl' }: { title: string; children: ReactNode; onClose: () => void; width?: string }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`card w-full ${width} max-h-[90vh] overflow-auto p-5 animate-fadeUp`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-display text-lg font-semibold">{title}</h2>
          <button onClick={onClose} className="text-muted hover:text-ink" aria-label="Close"><X size={18} /></button>
        </div>
        {children}
      </div>
    </div>
  )
}

export function Spinner({ className = '' }: { className?: string }) { return <Loader2 className={`animate-spin ${className}`} size={16} /> }

export function Loading({ rows = 3 }: { rows?: number }) {
  return <div className="space-y-3" aria-busy="true" aria-label="Loading">{Array.from({ length: rows }).map((_, i) => <div key={i} className="skeleton h-16" />)}</div>
}

export function Empty({ icon, title, hint, action }: { icon?: ReactNode; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="card border-dashed flex flex-col items-center text-center py-12 px-6 gap-2">
      <div className="text-muted">{icon}</div>
      <p className="font-display text-lg">{title}</p>
      {hint && <p className="text-sm text-muted max-w-md">{hint}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  const msg = error instanceof ApiError || error instanceof Error ? error.message : String(error)
  return (
    <div className="card border-danger/40 bg-danger/10 p-4 flex items-start gap-3">
      <AlertTriangle className="text-danger shrink-0" size={18} />
      <div className="flex-1"><p className="text-sm font-medium">Something went wrong</p><p className="text-sm text-muted">{msg}</p></div>
      {retry && <button className="btn-ghost" onClick={retry}>Retry</button>}
    </div>
  )
}

const STATUS: Record<string, string> = {
  draft: 'bg-muted/15 text-muted border-muted/30', uploaded: 'bg-accent/15 text-accent border-accent/30',
  processing: 'bg-primary/20 text-[#b79bff] border-primary/40', running: 'bg-primary/20 text-[#b79bff] border-primary/40',
  queued: 'bg-warn/15 text-warn border-warn/30', review_required: 'bg-warn/15 text-warn border-warn/30',
  revision_requested: 'bg-warn/15 text-warn border-warn/30', approved: 'bg-ok/15 text-ok border-ok/30',
  completed: 'bg-ok/15 text-ok border-ok/30', scheduled: 'bg-accent/15 text-accent border-accent/30',
  published: 'bg-teal/15 text-teal border-teal/30', failed: 'bg-danger/15 text-danger border-danger/30',
  rejected: 'bg-danger/15 text-danger border-danger/30', cancelled: 'bg-muted/15 text-muted border-muted/30',
  archived: 'bg-muted/10 text-muted border-muted/20', source: 'bg-accent/10 text-accent border-accent/20',
}
export const StatusBadge = ({ status }: { status: string }) => (
  <span className={`chip ${STATUS[status] || STATUS.draft}`}>{(status === 'running' || status === 'processing') && <Spinner className="!w-3 !h-3" />}{label(status)}</span>
)

export function ProgressBar({ value, className = '' }: { value: number; className?: string }) {
  return (
    <div className={`h-2 rounded-full bg-surface2 overflow-hidden ${className}`} role="progressbar" aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100}>
      <div className="h-full rounded-full bg-gradient-to-r from-primary via-accent to-teal transition-all duration-500" style={{ width: `${Math.max(2, Math.min(100, value))}%` }} />
    </div>
  )
}

export function Field({ label: l, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <div><label className="label" title={hint}>{l}</label>{children}{hint && <p className="text-[11px] text-muted mt-1">{hint}</p>}</div>
}

export function Tabs({ tabs, value, onChange }: { tabs: { id: string; label: string; count?: number }[]; value: string; onChange: (id: string) => void }) {
  return (
    <div className="flex gap-1 border-b border-line/70 overflow-x-auto" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}
          className={`px-3.5 py-2 text-sm whitespace-nowrap border-b-2 -mb-px transition ${value === t.id ? 'border-accent text-ink' : 'border-transparent text-muted hover:text-ink'}`}>
          {t.label}{t.count !== undefined && <span className="ml-1.5 text-xs text-muted">{t.count}</span>}
        </button>
      ))}
    </div>
  )
}

export function Tip({ text, children }: { text: string; children: ReactNode }) {
  return <span title={text} className="inline-flex">{children}</span>
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 mb-6">
      <div><h1 className="h-page">{title}</h1>{subtitle && <p className="text-sm text-muted mt-0.5">{subtitle}</p>}</div>
      <div className="flex flex-wrap gap-2">{actions}</div>
    </div>
  )
}

// ------------------------------- hooks -----------------------------------
export function useApi<T>(fn: () => Promise<T>, deps: unknown[] = [], pollMs = 0) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)
  const fnRef = useRef(fn)
  fnRef.current = fn
  const alive = useRef(true)
  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try { const d = await fnRef.current(); if (alive.current) { setData(d); setError(null) } }
    catch (e) { if (alive.current) setError(e) }
    finally { if (alive.current) setLoading(false) }
  }, [])
  useEffect(() => { alive.current = true; load(); return () => { alive.current = false } }, [...deps]) // eslint-disable-line
  useEffect(() => {
    if (!pollMs) return
    const t = setInterval(() => !document.hidden && load(true), pollMs)
    return () => clearInterval(t)
  }, [pollMs, load])
  return { data, error, loading, reload: () => load(true), setData }
}

export function useDebounced<T>(value: T, ms = 300) {
  const [v, setV] = useState(value)
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t) }, [value, ms])
  return v
}

export function Logo({ size = 36 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-label="RUHA">
      <defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#7C3AED" /><stop offset="1" stopColor="#22D3EE" /></linearGradient></defs>
      <rect width="64" height="64" rx="16" fill="rgb(var(--surface))" stroke="rgb(var(--line))" />
      <path d="M20 48V16h13a9 9 0 0 1 2 17.8L44 48" fill="none" stroke="url(#lg)" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
