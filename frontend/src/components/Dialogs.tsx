import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Link2, UploadCloud } from 'lucide-react'
import { api, DUB_LANGUAGES, LANGUAGES, Project } from '../api'
import { Field, Modal, ProgressBar, Spinner, fmtBytes, useApi, useToast } from '../ui'

export function NewProjectModal({ onClose, onCreated, withUpload = false }: { onClose: () => void; onCreated?: (p: Project) => void; withUpload?: boolean }) {
  const toast = useToast(), nav = useNavigate()
  const [f, setF] = useState({ name: '', description: '', content_type: 'sermon', tags: '' })
  const [file, setFile] = useState<File | null>(null)
  const [pct, setPct] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  const submit = async () => {
    if (!f.name.trim()) return toast.err('Give the project a name')
    setBusy(true)
    try {
      const p = await api.post<Project>('/api/projects', { ...f, tags: f.tags.split(',').map((t) => t.trim()).filter(Boolean) })
      if (file) {
        const form = new FormData(); form.append('file', file)
        setPct(0)
        try {
          const r = await api.upload<{ duplicates: { project_name: string }[] }>(`/api/projects/${p.id}/videos`, form, setPct)
          if (r.duplicates.length) toast.info(`This exact video already exists in: ${r.duplicates.map((d) => d.project_name).join(', ')}`)
        } catch (e) { toast.err(e); toast.info('Project created, but the video upload failed. You can retry from the project page.') }
      }
      toast.ok('Project created'); onCreated?.(p); onClose(); nav(`/projects/${p.id}`)
    } catch (e) { toast.err(e) } finally { setBusy(false); setPct(null) }
  }
  return (
    <Modal title="New project" onClose={onClose} width="max-w-lg">
      <div className="space-y-3">
        <Field label="Project name"><input className="input" autoFocus value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="e.g. Sunday Service - Grace" /></Field>
        <Field label="Description"><textarea className="input" rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Content type"><select className="input" value={f.content_type} onChange={(e) => setF({ ...f, content_type: e.target.value })}>
            {['sermon', 'teaching', 'interview', 'event', 'testimony', 'other'].map((t) => <option key={t}>{t}</option>)}</select></Field>
          <Field label="Tags" hint="Comma separated"><input className="input" value={f.tags} onChange={(e) => setF({ ...f, tags: e.target.value })} /></Field>
        </div>
        <div>
          <label className="label">Video (optional - you can upload later)</label>
          <button type="button" onClick={() => input.current?.click()} className="w-full rounded-xl border border-dashed border-line hover:border-accent/70 transition p-5 flex flex-col items-center gap-1 text-sm text-muted">
            <UploadCloud />{file ? <span className="text-ink">{file.name} · {fmtBytes(file.size)}</span> : 'Click to choose MP4, MOV, MKV or AVI'}
          </button>
          <input ref={input} type="file" hidden accept=".mp4,.mov,.mkv,.avi,.m4v,.webm,video/*" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          {pct !== null && <div className="mt-2"><ProgressBar value={pct} /><p className="text-xs text-muted mt-1">Uploading… {pct}%</p></div>}
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={submit} disabled={busy}>{busy && <Spinner />}Create project</button>
        </div>
      </div>
    </Modal>
  )
}

/** Paste a video link (YouTube etc.): downloads it, then runs the whole content pipeline. */
export function ImportLinkModal({ onClose }: { onClose: () => void }) {
  const toast = useToast(), nav = useNavigate()
  const [url, setUrl] = useState(''), [name, setName] = useState(''), [busy, setBusy] = useState(false), [language, setLanguage] = useState('English')
  const submit = async () => {
    if (!/^https?:\/\//i.test(url.trim())) return toast.err('Paste a full link starting with https://')
    setBusy(true)
    try {
      const r = await api.post<{ project_id: number }>('/api/import-url', { url: url.trim(), name, process: true, language })
      toast.ok('Downloading - content will be generated automatically'); onClose(); nav(`/projects/${r.project_id}`)
    } catch (e) { toast.err(e) } finally { setBusy(false) }
  }
  return (
    <Modal title="Create content from a link" onClose={onClose} width="max-w-lg">
      <div className="space-y-3">
        <Field label="Video link" hint="YouTube or another public video page"><input className="input" autoFocus value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && submit()} placeholder="https://www.youtube.com/watch?v=..." /></Field>
        <Field label="Project name (optional)" hint="Defaults to the video title"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Subtitle language" hint="Reel subtitles are written in this language (English by default)"><select className="input" value={language} onChange={(e) => setLanguage(e.target.value)}>{LANGUAGES.map((l) => <option key={l}>{l}</option>)}</select></Field>
        <p className="text-xs text-muted">The video is downloaded to this computer, then transcribed and turned into reels, a poster and posts locally. Only use videos you own or have permission to use.</p>
        <div className="flex justify-end gap-2 pt-2"><button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={submit} disabled={busy}>{busy ? <Spinner /> : <Link2 size={15} />}Download &amp; generate</button></div>
      </div>
    </Modal>
  )
}

/** Pick a project (with a video) then run a job type on it. */
export function RunJobModal({ mode, onClose, projectId }: { mode: 'generate' | 'reels' | 'poster' | 'translate'; onClose: () => void; projectId?: number }) {
  const toast = useToast(), nav = useNavigate()
  const { data, loading } = useApi(() => api.get<{ items: Project[] }>('/api/projects?limit=200'), [])
  const [pid, setPid] = useState<number | ''>(projectId ?? '')
  const [target, setTarget] = useState('Hindi')
  const [dub, setDub] = useState(false)
  const [voice, setVoice] = useState('Female')
  const [language, setLanguage] = useState('English')
  const [busy, setBusy] = useState(false)
  const titles = { generate: 'Generate content', reels: 'Create reels', poster: 'Create poster', translate: 'Translate video' }
  const projects = (data?.items ?? []).filter((p) => p.video)

  const go = async () => {
    if (!pid) return toast.err('Choose a project')
    setBusy(true)
    try {
      if (mode === 'generate') await api.post(`/api/projects/${pid}/process`, { language })
      else if (mode === 'reels') await api.post(`/api/projects/${pid}/process`, { steps: ['transcription', 'reel_generation'], language })
      else if (mode === 'poster') await api.post(`/api/projects/${pid}/process`, { steps: ['transcription', 'poster_generation'] })
      else await api.post(`/api/projects/${pid}/jobs`, { job_type: dub ? 'dubbing' : 'translation', params: { target, dub, voice, burn: false } })
      toast.ok('Queued - follow progress in Processing'); onClose(); nav('/processing')
    } catch (e) { toast.err(e) } finally { setBusy(false) }
  }
  return (
    <Modal title={titles[mode]} onClose={onClose} width="max-w-lg">
      <div className="space-y-3">
        <Field label="Project">
          <select className="input" value={pid} onChange={(e) => setPid(Number(e.target.value) || '')} disabled={!!projectId}>
            <option value="">{loading ? 'Loading…' : projects.length ? 'Choose a project with a video' : 'No project has a video yet'}</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </Field>
        {mode === 'translate' && (<>
          <Field label="Target language"><select className="input" value={target} onChange={(e) => { setTarget(e.target.value); if (!DUB_LANGUAGES.has(e.target.value)) setDub(false) }}>{LANGUAGES.map((l) => <option key={l}>{l}</option>)}</select></Field>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={dub} disabled={!DUB_LANGUAGES.has(target)} onChange={(e) => setDub(e.target.checked)} />
            Also dub the audio {!DUB_LANGUAGES.has(target) && <span className="text-xs text-muted">(voice not available for {target} - subtitles only)</span>}</label>
          {dub && <Field label="Voice"><select className="input" value={voice} onChange={(e) => setVoice(e.target.value)}><option>Female</option><option>Male</option></select></Field>}
          <p className="text-xs text-muted">Uses the existing El Ruach Video Translator (Whisper + IndicTrans2 + Parler-TTS). Models load one at a time.</p>
        </>)}
        {(mode === 'generate' || mode === 'reels') && <Field label="Subtitle language" hint="Subtitles on the reels (English by default)"><select className="input" value={language} onChange={(e) => setLanguage(e.target.value)}>{LANGUAGES.map((l) => <option key={l}>{l}</option>)}</select></Field>}
        {mode !== 'translate' && <p className="text-xs text-muted">Steps already completed (e.g. an existing transcript) are reused, not repeated.</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={go} disabled={busy || !pid}>{busy && <Spinner />}Start</button>
        </div>
      </div>
    </Modal>
  )
}

const THEME_CHOICES = ['auto', 'warfare', 'spirit', 'grace', 'peace', 'hope', 'faith', 'love', 'strength', 'growth']

/** Upload pictures and turn them into a reel with a hook card, captions and a closing card. */
export function ImageReelModal({ projectId, onClose, onDone }: { projectId: number; onClose: () => void; onDone: () => void }) {
  const toast = useToast()
  const imgs = useApi(() => api.get<{ name: string; size: number }[]>(`/api/projects/${projectId}/images`), [projectId])
  const input = useRef<HTMLInputElement>(null)
  const [sel, setSel] = useState<string[] | null>(null)
  const [caps, setCaps] = useState<Record<string, string>>({})
  const [hook, setHook] = useState(''), [verse, setVerse] = useState(''), [closing, setClosing] = useState('Follow for more')
  const [theme, setTheme] = useState('auto'), [secs, setSecs] = useState(3.5), [language, setLanguage] = useState('English')
  const [busy, setBusy] = useState(false)
  const list = imgs.data ?? []
  const chosen = sel ?? list.map((i) => i.name)

  const upload = async (files: FileList | null) => {
    if (!files?.length) return
    const form = new FormData(); Array.from(files).forEach((f) => form.append('files', f))
    try { await api.upload(`/api/projects/${projectId}/images`, form); toast.ok('Images uploaded'); setSel(null); imgs.reload() } catch (e) { toast.err(e) }
    if (input.current) input.current.value = ''
  }
  const remove = async (name: string) => { try { await api.del(`/api/projects/${projectId}/images/${encodeURIComponent(name)}`); setSel(null); imgs.reload() } catch (e) { toast.err(e) } }
  const go = async () => {
    if (!chosen.length) return toast.err('Upload and select at least one image')
    setBusy(true)
    try {
      await api.post(`/api/projects/${projectId}/jobs`, { job_type: 'image_reel', params: {
        images: chosen, hook, verse, closing, language, seconds_per_image: secs, theme: theme === 'auto' ? undefined : theme,
        captions: chosen.map((n) => caps[n] || '') } })
      toast.ok('Queued - the reel will appear in this project when ready'); onDone(); onClose()
    } catch (e) { toast.err(e) } finally { setBusy(false) }
  }
  return (
    <Modal title="Create a reel from images" onClose={onClose} width="max-w-3xl">
      <div className="space-y-3">
        <div className="flex items-center gap-3 flex-wrap"><button className="btn-ghost" onClick={() => input.current?.click()}><UploadCloud size={16} />Add images</button>
          <span className="text-xs text-muted">JPG, PNG or WEBP · up to 25 MB each · shown in the order listed</span>
          <input ref={input} type="file" hidden multiple accept=".jpg,.jpeg,.png,.webp,image/*" onChange={(e) => upload(e.target.files)} /></div>
        {list.length === 0 ? <p className="text-sm text-muted card p-4">No images yet. Add a few pictures to begin.</p> : (
          <div className="grid sm:grid-cols-2 gap-2 max-h-72 overflow-y-auto pr-1">
            {list.map((i) => (
              <div key={i.name} className="card p-2 flex gap-2 items-start">
                <input type="checkbox" className="mt-1 accent-[rgb(var(--primary))]" checked={chosen.includes(i.name)} aria-label={`Use ${i.name}`}
                  onChange={(e) => setSel(e.target.checked ? [...chosen, i.name] : chosen.filter((n) => n !== i.name))} />
                <img src={`/api/projects/${projectId}/images/${encodeURIComponent(i.name)}`} alt="" className="w-16 h-16 object-cover rounded-lg shrink-0" />
                <div className="min-w-0 flex-1"><p className="text-xs truncate">{i.name}</p>
                  <input className="input !py-1 mt-1 text-xs" placeholder="Caption for this image (optional)" value={caps[i.name] || ''} onChange={(e) => setCaps({ ...caps, [i.name]: e.target.value })} /></div>
                <button className="text-xs text-muted hover:text-danger" onClick={() => remove(i.name)} aria-label={`Delete ${i.name}`}>✕</button>
              </div>))}
          </div>)}
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Hook text (opening image)" hint="A short, powerful line. Empty = project name"><input className="input" value={hook} onChange={(e) => setHook(e.target.value)} /></Field>
          <Field label="Verse (optional)"><input className="input" value={verse} onChange={(e) => setVerse(e.target.value)} placeholder="e.g. Psalm 46:10" /></Field>
          <Field label="Closing line"><input className="input" value={closing} onChange={(e) => setClosing(e.target.value)} /></Field>
          <Field label="Seconds per image"><input className="input" type="number" min={1.5} max={12} step={0.5} value={secs} onChange={(e) => setSecs(Number(e.target.value) || 3.5)} /></Field>
          <Field label="Theme background"><select className="input" value={theme} onChange={(e) => setTheme(e.target.value)}>{THEME_CHOICES.map((t) => <option key={t}>{t}</option>)}</select></Field>
          <Field label="Caption language"><select className="input" value={language} onChange={(e) => setLanguage(e.target.value)}>{LANGUAGES.map((l) => <option key={l}>{l}</option>)}</select></Field>
        </div>
        <div className="flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={go} disabled={busy || !chosen.length}>{busy && <Spinner />}Create reel</button></div>
      </div>
    </Modal>
  )
}
