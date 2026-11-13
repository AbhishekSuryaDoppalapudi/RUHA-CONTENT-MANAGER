import { useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, Plus, Trash2 } from 'lucide-react'
import { api, Asset, CalEntry } from '../api'
import { ErrorBox, Field, Loading, Modal, PageHeader, Spinner, StatusBadge, label, useApi, useConfirm, useToast } from '../ui'

const STAT_COLOR: Record<string, string> = { draft: 'border-muted/50 bg-muted/10', approved: 'border-ok/50 bg-ok/10', scheduled: 'border-accent/60 bg-accent/10', published: 'border-teal/60 bg-teal/10' }
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const addDays = (d: Date, n: number) => { const x = new Date(d); x.setDate(x.getDate() + n); return x }
const startOfWeek = (d: Date) => addDays(d, -((d.getDay() + 6) % 7))

export default function Calendar() {
  const [mode, setMode] = useState<'month' | 'week' | 'day'>('month')
  const [cursor, setCursor] = useState(new Date())
  const [campaign, setCampaign] = useState(''); const [status, setStatus] = useState('')
  const [edit, setEdit] = useState<Partial<CalEntry> | null>(null)

  const range = useMemo(() => {
    if (mode === 'day') return [cursor, addDays(cursor, 1)]
    if (mode === 'week') { const s = startOfWeek(cursor); return [s, addDays(s, 7)] }
    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1); const s = startOfWeek(first)
    return [s, addDays(s, 42)]
  }, [mode, cursor])
  const { data, error, loading, reload } = useApi(() => api.get<CalEntry[]>(`/api/calendar?start=${iso(range[0])}&end=${iso(range[1])}${campaign ? `&campaign=${encodeURIComponent(campaign)}` : ''}${status ? `&status=${status}` : ''}`), [iso(range[0]), mode, campaign, status])
  const meta = useApi(() => api.get<{ campaigns: string[] }>('/api/calendar-meta/campaigns'), [data?.length])
  const byDay = useMemo(() => { const m: Record<string, CalEntry[]> = {}; (data ?? []).forEach((e) => (m[e.scheduled_for.slice(0, 10)] ||= []).push(e)); return m }, [data])
  const move = (n: number) => setCursor(mode === 'month' ? new Date(cursor.getFullYear(), cursor.getMonth() + n, 1) : addDays(cursor, n * (mode === 'week' ? 7 : 1)))
  const title = mode === 'month' ? cursor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' }) : mode === 'week' ? `${range[0].toLocaleDateString(undefined, { day: 'numeric', month: 'short' })} – ${addDays(range[1], -1).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}` : cursor.toLocaleDateString(undefined, { dateStyle: 'full' })
  const newOn = (d: Date) => setEdit({ title: '', scheduled_for: `${iso(d)}T09:00:00`, status: 'draft', campaign: '', category: '', platform: '', notes: '' })
  const days = Array.from({ length: mode === 'month' ? 42 : mode === 'week' ? 7 : 1 }, (_, i) => addDays(range[0], i))

  return (
    <>
      <PageHeader title="Content calendar" subtitle="Plan when approved assets go out. Publishing is manual - no social accounts are connected." actions={<button className="btn-primary" onClick={() => newOn(cursor)}><Plus size={16} />New entry</button>} />
      <div className="card p-3 mb-4 flex flex-wrap items-center gap-2">
        <button className="btn-ghost !p-2" onClick={() => move(-1)} aria-label="Previous"><ChevronLeft size={16} /></button>
        <button className="btn-ghost" onClick={() => setCursor(new Date())}>Today</button>
        <button className="btn-ghost !p-2" onClick={() => move(1)} aria-label="Next"><ChevronRight size={16} /></button>
        <h2 className="font-display text-lg ml-2">{title}</h2>
        <div className="ml-auto flex gap-2 flex-wrap">
          <select className="input !w-auto" value={campaign} onChange={(e) => setCampaign(e.target.value)} aria-label="Campaign"><option value="">All campaigns</option>{meta.data?.campaigns.map((c) => <option key={c}>{c}</option>)}</select>
          <select className="input !w-auto" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status"><option value="">All statuses</option>{['draft', 'approved', 'scheduled', 'published'].map((s) => <option key={s} value={s}>{label(s)}</option>)}</select>
          {(['month', 'week', 'day'] as const).map((m) => <button key={m} className={mode === m ? 'btn-primary' : 'btn-ghost'} onClick={() => setMode(m)}>{label(m)}</button>)}</div>
      </div>
      {error ? <ErrorBox error={error} retry={reload} /> : loading && !data ? <Loading /> : (
        <div className={mode === 'day' ? '' : 'grid grid-cols-7 gap-px rounded-2xl overflow-hidden border border-line/70 bg-line/50'}>
          {mode !== 'day' && ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d) => <div key={d} className="bg-surface2/80 text-xs text-muted text-center py-2">{d}</div>)}
          {days.map((d) => {
            const k = iso(d); const list = byDay[k] ?? []; const other = mode === 'month' && d.getMonth() !== cursor.getMonth(); const today = k === iso(new Date())
            return (
              <div key={k} className={`${mode === 'day' ? 'card' : 'bg-surface/90'} ${mode === 'month' ? 'min-h-[110px]' : 'min-h-[260px]'} p-2 ${other ? 'opacity-45' : ''}`}>
                <div className="flex items-center justify-between mb-1"><span className={`text-xs ${today ? 'bg-primary text-white rounded-full px-2 py-0.5' : 'text-muted'}`}>{mode === 'day' ? d.toLocaleDateString(undefined, { dateStyle: 'full' }) : d.getDate()}</span>
                  <button className="text-muted hover:text-accent" onClick={() => newOn(d)} aria-label={`Add entry on ${k}`}><Plus size={14} /></button></div>
                <div className="space-y-1">{list.map((e) => (
                  <button key={e.id} onClick={() => setEdit(e)} className={`w-full text-left rounded-lg border px-2 py-1 text-xs ${STAT_COLOR[e.status]} hover:brightness-125`}>
                    <span className="block truncate font-medium">{e.title}</span><span className="block truncate text-muted">{e.scheduled_for.slice(11, 16)}{e.campaign ? ` · ${e.campaign}` : ''}</span></button>))}</div>
                {list.length === 0 && mode === 'day' && <p className="text-sm text-muted mt-6">Nothing scheduled for this day.</p>}
              </div>)
          })}
        </div>)}
      <div className="flex gap-3 mt-3 text-xs text-muted">{Object.keys(STAT_COLOR).map((s) => <span key={s} className="flex items-center gap-1.5"><span className={`h-3 w-3 rounded border ${STAT_COLOR[s]}`} />{label(s)}</span>)}</div>
      {edit && <EntryModal entry={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload() }} />}
    </>
  )
}

function EntryModal({ entry, onClose, onSaved }: { entry: Partial<CalEntry>; onClose: () => void; onSaved: () => void }) {
  const toast = useToast(), confirm = useConfirm()
  const [f, setF] = useState({ ...entry, reminder_at: entry.reminder_at ?? '' } as any)
  const [busy, setBusy] = useState(false)
  const assets = useApi(() => api.get<Asset[]>('/api/assets?kind=main_reel,short_reel,poster,social_post'), [])
  const save = async () => {
    if (!f.title?.trim()) return toast.err('Enter a title')
    setBusy(true)
    try {
      const body = { title: f.title, scheduled_for: f.scheduled_for.slice(0, 19), status: f.status, campaign: f.campaign, category: f.category, platform: f.platform, notes: f.notes, asset_id: f.asset_id ?? null, reminder_at: f.reminder_at || null }
      if (entry.id) await api.patch(`/api/calendar/${entry.id}`, { ...body, asset_id: f.asset_id ?? undefined, reminder_at: f.reminder_at || undefined, clear_reminder: !f.reminder_at })
      else await api.post('/api/calendar', body)
      toast.ok('Saved'); onSaved()
    } catch (e) { toast.err(e) } finally { setBusy(false) }
  }
  return (
    <Modal title={entry.id ? 'Edit entry' : 'New calendar entry'} onClose={onClose} width="max-w-xl">
      <div className="grid sm:grid-cols-2 gap-3">
        <div className="sm:col-span-2"><Field label="Title"><input className="input" autoFocus value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field></div>
        <Field label="Date & time"><input type="datetime-local" className="input" value={f.scheduled_for?.slice(0, 16)} onChange={(e) => setF({ ...f, scheduled_for: e.target.value + ':00' })} /></Field>
        <Field label="Status" hint="Approved, scheduled and published need an approved asset."><select className="input" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>{['draft', 'approved', 'scheduled', 'published'].map((s) => <option key={s} value={s}>{label(s)}</option>)}</select></Field>
        <div className="sm:col-span-2"><Field label="Asset"><select className="input" value={f.asset_id ?? ''} onChange={(e) => setF({ ...f, asset_id: e.target.value ? Number(e.target.value) : null })}>
          <option value="">No asset linked</option>{(assets.data ?? []).map((a) => <option key={a.id} value={a.id}>{label(a.kind)} · {a.title || a.slot} ({a.status})</option>)}</select></Field></div>
        <Field label="Campaign"><input className="input" value={f.campaign} onChange={(e) => setF({ ...f, campaign: e.target.value })} /></Field>
        <Field label="Category"><input className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} placeholder="Reel, Poster, Quote…" /></Field>
        <Field label="Platform (note only)"><input className="input" value={f.platform} onChange={(e) => setF({ ...f, platform: e.target.value })} placeholder="Instagram, YouTube…" /></Field>
        <Field label="Reminder"><input type="datetime-local" className="input" value={(f.reminder_at || '').slice(0, 16)} onChange={(e) => setF({ ...f, reminder_at: e.target.value ? e.target.value + ':00' : '' })} /></Field>
        <div className="sm:col-span-2"><Field label="Notes"><textarea className="input" rows={3} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field></div>
      </div>
      <div className="flex justify-between pt-4">
        {entry.id ? <button className="btn-danger" onClick={async () => { if (await confirm({ title: 'Delete this entry?', confirm: 'Delete', danger: true })) { await api.del(`/api/calendar/${entry.id}`); toast.ok('Deleted'); onSaved() } }}><Trash2 size={15} />Delete</button> : <span />}
        <div className="flex gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button><button className="btn-primary" onClick={save} disabled={busy}>{busy && <Spinner />}Save</button></div>
      </div>
    </Modal>)
}
