import { useRef, useState } from 'react'
import { ImagePlus, Pencil, Plus, Trash2, Upload } from 'lucide-react'
import { api } from '../api'
import { ErrorBox, Loading, useApi, useConfirm, useToast } from '../ui'

type Text = { id: string; kind: string; name: string; text: string }
type Kit = { has_logo: boolean; texts: Text[]; kinds: Record<string, string> }

const HELP: Record<string, string> = {
  closing: 'Shown on the closing image of every reel in this project (replaces "Follow for more").',
  caption: 'Added under the caption of every reel and post, e.g. a call to action, website or channel line.',
  hashtags: 'Added to the hashtags of every reel, poster and post, e.g. #ElRuach #Sermon.',
}

/** Per-project logo and reusable text templates (closing line, caption footer, hashtags). */
export default function BrandKit({ pid }: { pid: number }) {
  const toast = useToast(), confirm = useConfirm()
  const { data: kit, error, loading, reload } = useApi(() => api.get<Kit>(`/api/projects/${pid}/brand-kit`), [pid])
  const [stamp, setStamp] = useState(Date.now())
  const [kind, setKind] = useState('closing'), [name, setName] = useState(''), [text, setText] = useState('')
  const [editing, setEditing] = useState<Text | null>(null)
  const [busy, setBusy] = useState('')
  const logoFile = useRef<HTMLInputElement>(null), textFile = useRef<HTMLInputElement>(null)

  if (loading && !kit) return <Loading rows={2} />
  if (error && !kit) return <ErrorBox error={error} retry={reload} />
  if (!kit) return null

  const run = async (key: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(key)
    try { await fn(); toast.ok(ok); reload() } catch (e) { toast.err(e) } finally { setBusy('') }
  }
  const uploadLogo = (f: File) => {
    const form = new FormData(); form.append('file', f)
    run('logo', () => api.upload(`/api/projects/${pid}/brand-kit/logo`, form).then(() => setStamp(Date.now())), 'Logo saved - new designs and reels use it')
    if (logoFile.current) logoFile.current.value = ''
  }
  const uploadTexts = (f: File) => {
    const form = new FormData(); form.append('file', f)
    run('texts', () => api.upload(`/api/projects/${pid}/brand-kit/texts/upload?kind=${kind}`, form), 'Text templates imported')
    if (textFile.current) textFile.current.value = ''
  }
  const add = () => run('add', async () => { await api.post(`/api/projects/${pid}/brand-kit/texts`, { kind, name, text }); setName(''); setText('') }, 'Text template added')
  const save = () => editing && run('save', async () => { await api.put(`/api/projects/${pid}/brand-kit/texts/${editing.id}`, editing); setEditing(null) }, 'Saved')
  const remove = async (t: Text) => {
    if (await confirm({ title: 'Delete this text template?', confirm: 'Delete', danger: true })) run('del', () => api.del(`/api/projects/${pid}/brand-kit/texts/${t.id}`), 'Deleted')
  }

  return (
    <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-5">
      <section className="card p-4 space-y-3 self-start">
        <h2 className="font-display text-lg">Organisation logo</h2>
        <div className="rounded-xl bg-surface2 border border-line h-40 flex items-center justify-center overflow-hidden">
          {kit.has_logo ? <img src={`/api/projects/${pid}/brand-kit/logo?t=${stamp}`} alt="Project logo" className="max-h-36 max-w-full object-contain p-2" />
            : <p className="text-xs text-muted text-center px-4">No logo for this project. The organisation logo from Brand &amp; Templates is used, if any.</p>}
        </div>
        <input ref={logoFile} type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={(e) => e.target.files?.[0] && uploadLogo(e.target.files[0])} />
        <div className="flex gap-2">
          <button className="btn-primary" disabled={busy === 'logo'} onClick={() => logoFile.current?.click()}><ImagePlus size={15} />{kit.has_logo ? 'Replace logo' : 'Upload logo'}</button>
          {kit.has_logo && <button className="btn-danger" disabled={busy === 'rmlogo'} onClick={async () => {
            if (await confirm({ title: 'Remove this project’s logo?', body: 'Designs will use the organisation logo again.', confirm: 'Remove', danger: true })) run('rmlogo', () => api.del(`/api/projects/${pid}/brand-kit/logo`).then(() => setStamp(Date.now())), 'Logo removed')
          }}><Trash2 size={15} />Remove</button>}
        </div>
        <p className="text-xs text-muted">PNG with a transparent background works best. It appears on posters, hook/closing images, creatives and as the reel watermark for this project only. Existing items change the next time they are generated or re-rendered.</p>
      </section>

      <section className="card p-4 space-y-4">
        <h2 className="font-display text-lg">Text templates</h2>
        <div className="grid sm:grid-cols-[180px_1fr] gap-2">
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Template type">
            {Object.entries(kit.kinds).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          <input className="input" placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
          <span className="hidden sm:block" />
          <textarea className="input min-h-[72px]" placeholder={kind === 'hashtags' ? '#ElRuach #Sermon #Faith' : kind === 'closing' ? 'Follow El Ruach for more' : 'Watch the full message on our YouTube channel.'}
            value={text} onChange={(e) => setText(e.target.value)} />
        </div>
        <p className="text-xs text-muted -mt-2">{HELP[kind]}</p>
        <input ref={textFile} type="file" accept=".txt,.md,.json" className="hidden" onChange={(e) => e.target.files?.[0] && uploadTexts(e.target.files[0])} />
        <div className="flex flex-wrap gap-2">
          <button className="btn-primary" disabled={!text.trim() || busy === 'add'} onClick={add}><Plus size={15} />Add template</button>
          <button className="btn-ghost" disabled={busy === 'texts'} onClick={() => textFile.current?.click()} title="A .txt file (one template per line) or a .json file"><Upload size={15} />Upload file</button>
        </div>

        {kit.texts.length === 0 ? <p className="text-sm text-muted">No text templates yet. Add them here or upload a .txt (one per line) or .json file.</p> : (
          Object.entries(kit.kinds).map(([k, label]) => { const list = kit.texts.filter((t) => t.kind === k); return list.length ? (
            <div key={k}><p className="text-xs uppercase tracking-wider text-muted mb-1">{label} <span className="normal-case">({list.length})</span></p>
              <ul className="space-y-2">{list.map((t) => (
                <li key={t.id} className="rounded-xl border border-line p-3">
                  {editing?.id === t.id ? (
                    <div className="space-y-2">
                      <input className="input" value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
                      <textarea className="input min-h-[64px]" value={editing.text} onChange={(e) => setEditing({ ...editing, text: e.target.value })} />
                      <div className="flex gap-2"><button className="btn-primary" onClick={save}>Save</button><button className="btn-ghost" onClick={() => setEditing(null)}>Cancel</button></div>
                    </div>
                  ) : (
                    <div className="flex items-start gap-2">
                      <div className="min-w-0 flex-1"><p className="text-sm font-medium truncate">{t.name}</p><p className="text-xs text-muted whitespace-pre-wrap break-words">{t.text}</p></div>
                      <button className="btn-ghost !p-1.5" aria-label="Edit" onClick={() => setEditing(t)}><Pencil size={14} /></button>
                      <button className="btn-danger !p-1.5" aria-label="Delete" onClick={() => remove(t)}><Trash2 size={14} /></button>
                    </div>)}
                </li>))}</ul></div>) : null })
        )}
        <p className="text-xs text-muted">If you add several of one type, the first is used. Closing lines apply to reels made or re-rendered afterwards.</p>
      </section>
    </div>
  )
}
