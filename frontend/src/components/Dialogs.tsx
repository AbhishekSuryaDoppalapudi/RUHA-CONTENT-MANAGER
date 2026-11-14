import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { UploadCloud } from 'lucide-react'
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

/** Pick a project (with a video) then run a job type on it. */
export function RunJobModal({ mode, onClose, projectId }: { mode: 'generate' | 'reels' | 'poster' | 'translate'; onClose: () => void; projectId?: number }) {
  const toast = useToast(), nav = useNavigate()
  const { data, loading } = useApi(() => api.get<{ items: Project[] }>('/api/projects?limit=200'), [])
  const [pid, setPid] = useState<number | ''>(projectId ?? '')
  const [target, setTarget] = useState('Hindi')
  const [dub, setDub] = useState(false)
  const [voice, setVoice] = useState('Female')
  const [busy, setBusy] = useState(false)
  const titles = { generate: 'Generate content', reels: 'Create reels', poster: 'Create poster', translate: 'Translate video' }
  const projects = (data?.items ?? []).filter((p) => p.video)

  const go = async () => {
    if (!pid) return toast.err('Choose a project')
    setBusy(true)
    try {
      if (mode === 'generate') await api.post(`/api/projects/${pid}/process`, {})
      else if (mode === 'reels') await api.post(`/api/projects/${pid}/process`, { steps: ['transcription', 'reel_generation'] })
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
        {mode !== 'translate' && <p className="text-xs text-muted">Steps already completed (e.g. an existing transcript) are reused, not repeated.</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={go} disabled={busy || !pid}>{busy && <Spinner />}Start</button>
        </div>
      </div>
    </Modal>
  )
}
