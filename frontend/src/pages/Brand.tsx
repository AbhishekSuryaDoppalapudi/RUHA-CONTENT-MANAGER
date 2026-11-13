import { useEffect, useRef, useState } from 'react'
import { Plus, Save, Trash2, Upload } from 'lucide-react'
import { api } from '../api'
import { ErrorBox, Field, Loading, PageHeader, Spinner, label, useApi, useConfirm, useToast } from '../ui'

const COLORS = ['background', 'background2', 'primary', 'accent', 'accent2', 'text', 'muted']
const SLOTS: [string, string, string][] = [['logo', 'Logo', 'image/*'], ['watermark', 'Watermark (on reels)', 'image/*'], ['intro', 'Video intro', 'video/*'], ['outro', 'Video outro', 'video/*'],
  ['font_heading', 'Heading font (.ttf/.otf)', '.ttf,.otf'], ['font_body', 'Body font (.ttf/.otf)', '.ttf,.otf']]

export default function Brand() {
  const toast = useToast(), confirm = useConfirm()
  const { data, error, loading, reload } = useApi(() => api.get<any>('/api/brand'), [])
  const tpl = useApi(() => api.get<any[]>('/api/templates'), [])
  const [f, setF] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [stamp, setStamp] = useState(0)
  useEffect(() => { if (data) setF({ name: data.name, tagline: data.tagline, colors: data.colors, export: data.export }) }, [data])
  if (loading && !data) return <Loading />
  if (error) return <ErrorBox error={error} retry={reload} />
  if (!f) return null
  const save = async () => { setBusy(true); try { await api.put('/api/brand', f); toast.ok('Brand saved - new graphics use it immediately'); reload() } catch (e) { toast.err(e) } finally { setBusy(false) } }
  const upload = async (slot: string, file?: File) => { if (!file) return; const fd = new FormData(); fd.append('file', file); try { await api.upload(`/api/brand/files/${slot}`, fd); toast.ok('Uploaded'); setStamp(Date.now()); reload() } catch (e) { toast.err(e) } }
  const clear = async (slot: string) => { try { await api.del(`/api/brand/files/${slot}`); toast.ok('Removed'); reload() } catch (e) { toast.err(e) } }
  const setExp = (k: string, i: number, v: number) => setF({ ...f, export: { ...f.export, [k]: f.export[k].map((x: number, j: number) => (j === i ? v : x)) } })

  return (
    <>
      <PageHeader title="Brand & templates" subtitle="Everything generated - posters, posts and reels - follows this identity." actions={<button className="btn-primary" onClick={save} disabled={busy}>{busy ? <Spinner /> : <Save size={15} />}Save brand</button>} />
      <div className="grid lg:grid-cols-2 gap-5">
        <section className="card p-4 space-y-3"><h2 className="font-display text-lg">Identity</h2>
          <Field label="Brand name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Tagline"><input className="input" value={f.tagline} onChange={(e) => setF({ ...f, tagline: e.target.value })} /></Field>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">{COLORS.map((c) => (
            <Field key={c} label={label(c)}><div className="flex gap-2"><input type="color" className="h-10 w-12 rounded-lg bg-transparent border border-line cursor-pointer" value={f.colors[c]} onChange={(e) => setF({ ...f, colors: { ...f.colors, [c]: e.target.value.toUpperCase() } })} />
              <input className="input !px-2 font-mono text-xs" value={f.colors[c]} onChange={(e) => setF({ ...f, colors: { ...f.colors, [c]: e.target.value } })} /></div></Field>))}</div>
          <h3 className="font-display pt-2">Default export sizes (px)</h3>
          <div className="grid grid-cols-3 gap-3">{['reel', 'poster', 'social'].map((k) => (
            <Field key={k} label={label(k)}><div className="flex gap-1"><input className="input !px-2" type="number" value={f.export[k][0]} onChange={(e) => setExp(k, 0, Number(e.target.value))} aria-label={`${k} width`} /><input className="input !px-2" type="number" value={f.export[k][1]} onChange={(e) => setExp(k, 1, Number(e.target.value))} aria-label={`${k} height`} /></div></Field>))}</div>
          <p className="text-xs text-muted">Reel size used for rendering is set in Processing Centre → Output.</p>
        </section>

        <section className="card p-4 space-y-3"><h2 className="font-display text-lg">Brand files</h2>
          {SLOTS.map(([slot, title, accept]) => <FileSlot key={slot} slot={slot} title={title} accept={accept} has={data.files[slot]} stamp={stamp} onUpload={(file) => upload(slot, file)} onClear={() => clear(slot)} />)}
          <p className="text-xs text-muted">Watermark, intro and outro are added when reels are rendered (toggle in Settings → Export). Fonts apply to posters and posts.</p>
        </section>

        <section className="card p-4 lg:col-span-2"><div className="flex items-center justify-between mb-3"><h2 className="font-display text-lg">Templates</h2><NewTemplate onDone={tpl.reload} /></div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">{(tpl.data ?? []).map((t) => (
            <div key={t.id} className="rounded-xl border border-line/70 p-3 flex items-start gap-3"><div className="flex-1 min-w-0"><p className="font-medium truncate">{t.name}</p>
              <p className="text-xs text-muted">{label(t.kind)}{t.config.layout ? ` · ${t.config.layout} layout` : ''}{t.is_default ? ' · default' : ''}</p></div>
              <button className="btn-ghost !p-1.5" aria-label="Delete template" onClick={async () => { if (await confirm({ title: `Delete template “${t.name}”?`, confirm: 'Delete', danger: true })) { await api.del(`/api/templates/${t.id}`); tpl.reload() } }}><Trash2 size={14} /></button></div>))}</div>
          <p className="text-xs text-muted mt-3">Social-post templates rotate per post (post 1 uses the first, post 2 the second) so each gets a separate design.</p>
        </section>
      </div>
    </>
  )
}

function FileSlot({ slot, title, accept, has, stamp, onUpload, onClear }: { slot: string; title: string; accept: string; has: boolean; stamp: number; onUpload: (f?: File) => void; onClear: () => void }) {
  const ref = useRef<HTMLInputElement>(null)
  return (
    <div className="flex items-center gap-3 rounded-xl border border-line/70 p-2.5">
      <div className="h-12 w-16 rounded-lg bg-surface2 flex items-center justify-center overflow-hidden text-xs text-muted shrink-0">
        {has && (slot === 'logo' || slot === 'watermark') ? <img src={`/api/brand/files/${slot}?t=${stamp}`} className="max-h-full max-w-full object-contain" alt="" /> : has ? 'Set' : 'None'}</div>
      <div className="flex-1 min-w-0"><p className="text-sm">{title}</p><p className="text-xs text-muted">{has ? 'Uploaded' : 'Not set'}</p></div>
      <input ref={ref} type="file" hidden accept={accept} onChange={(e) => { onUpload(e.target.files?.[0]); e.target.value = '' }} />
      <button className="btn-ghost" onClick={() => ref.current?.click()}><Upload size={14} />{has ? 'Replace' : 'Upload'}</button>
      {has && <button className="btn-ghost !p-2" onClick={onClear} aria-label={`Remove ${title}`}><Trash2 size={14} /></button>}
    </div>)
}

function NewTemplate({ onDone }: { onDone: () => void }) {
  const toast = useToast(); const [open, setOpen] = useState(false)
  const [t, setT] = useState({ name: '', kind: 'social', layout: 'classic' })
  if (!open) return <button className="btn-ghost" onClick={() => setOpen(true)}><Plus size={15} />New template</button>
  return (
    <div className="flex gap-2 flex-wrap items-center">
      <input className="input !w-44" placeholder="Template name" value={t.name} onChange={(e) => setT({ ...t, name: e.target.value })} />
      <select className="input !w-auto" value={t.kind} onChange={(e) => setT({ ...t, kind: e.target.value })}><option value="social">Social post</option><option value="poster">Poster</option></select>
      <select className="input !w-auto" value={t.layout} onChange={(e) => setT({ ...t, layout: e.target.value })}><option>classic</option><option>band</option><option>centered</option></select>
      <button className="btn-primary" onClick={async () => { if (!t.name.trim()) return toast.err('Name the template'); try { await api.post('/api/templates', { kind: t.kind, name: t.name, config: { layout: t.layout } }); setOpen(false); setT({ ...t, name: '' }); onDone() } catch (e) { toast.err(e) } }}>Add</button>
      <button className="btn-ghost" onClick={() => setOpen(false)}>Cancel</button></div>)
}
