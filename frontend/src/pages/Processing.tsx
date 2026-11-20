import { useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, Cpu, HardDrive, MemoryStick, MonitorCog, Pause, Play, RotateCw, Save, Settings2, XCircle, Zap } from 'lucide-react'
import { api, Job, qs } from '../api'
import { Empty, ErrorBox, Field, Loading, Modal, PageHeader, ProgressBar, Spinner, StatusBadge, Tabs, ago, fmtDate, label, useApi, useToast } from '../ui'

export default function Processing() {
  const toast = useToast()
  const [tab, setTab] = useState('queue')
  const sys = useApi(() => api.get<any>('/api/system/status'), [], 4000)
  const jobsApi = useApi(() => api.get<{ total: number; paused: boolean; items: Job[] }>('/api/jobs?limit=100'), [], 2500)
  const [open, setOpen] = useState<number | null>(null)
  const [filter, setFilter] = useState('')
  const s = sys.data
  const act = async (fn: () => Promise<any>, ok: string) => { try { await fn(); toast.ok(ok); jobsApi.reload(); sys.reload() } catch (e) { toast.err(e) } }
  const paused = jobsApi.data?.paused
  const items = (jobsApi.data?.items ?? []).filter((j) => !filter || j.status === filter)
  const failed = (jobsApi.data?.items ?? []).filter((j) => j.status === 'failed').length

  return (
    <>
      <PageHeader title="AI Processing Centre" subtitle="Models, hardware and the job queue. Heavy models load one at a time to protect your 16 GB of RAM." actions={<>
        {paused ? <button className="btn-primary" onClick={() => act(() => api.post('/api/queue/resume'), 'Queue resumed')}><Play size={15} />Resume queue</button>
          : <button className="btn-ghost" onClick={() => act(() => api.post('/api/queue/pause'), 'Queue paused - the running job will finish first')}><Pause size={15} />Pause queue</button>}
        <button className="btn-ghost" disabled={!failed} onClick={() => act(() => api.post('/api/queue/retry-failed'), 'Failed jobs re-queued')}><RotateCw size={15} />Retry failed ({failed})</button></>} />
      {sys.error && !s ? <ErrorBox error={sys.error} retry={sys.reload} /> : !s ? <Loading rows={2} /> : <Status s={s} />}
      <div className="mt-6"><Tabs value={tab} onChange={setTab} tabs={[{ id: 'queue', label: 'Job queue', count: jobsApi.data?.total }, { id: 'config', label: 'Model & quality settings' }]} /></div>
      <div className="mt-5">
        {tab === 'queue' && (<>
          <div className="flex gap-1.5 mb-3 flex-wrap">{['', 'running', 'queued', 'completed', 'failed', 'cancelled'].map((f) => (
            <button key={f} onClick={() => setFilter(f)} className={`chip !px-3 !py-1 ${filter === f ? 'bg-primary/25 border-primary/60' : 'border-line text-muted hover:text-ink'}`}>{f ? label(f) : 'All'}</button>))}</div>
          {jobsApi.loading && !jobsApi.data ? <Loading /> : items.length === 0 ? <Empty icon={<Cpu size={34} />} title="No jobs" hint="Jobs appear here when you generate content, translate or re-render." /> : (
            <div className="space-y-2">{items.map((j) => (
              <div key={j.id} className="card p-3">
                <div className="flex items-center gap-3 flex-wrap"><b className="text-sm">{label(j.job_type)}</b><StatusBadge status={j.status} />
                  <Link to={`/projects/${j.project_id}`} className="text-sm text-accent hover:underline">{j.project_name}</Link><span className="text-xs text-muted">job #{j.id} · {ago(j.created_at)}{j.attempts > 1 ? ` · attempt ${j.attempts}` : ''}</span>
                  <span className="ml-auto flex gap-2">
                    <button className="btn-ghost !py-1" onClick={() => setOpen(j.id)}>Logs</button>
                    {(j.status === 'failed' || j.status === 'cancelled') && <button className="btn-ghost !py-1" onClick={() => act(() => api.post(`/api/jobs/${j.id}/retry`), 'Retry queued')}><RotateCw size={14} />Retry</button>}
                    {(j.status === 'queued' || j.status === 'running') && <button className="btn-ghost !py-1" onClick={() => act(() => api.post(`/api/jobs/${j.id}/cancel`), 'Cancelling…')}>Cancel</button>}</span></div>
                {j.status === 'running' && <div className="mt-2"><div className="flex justify-between text-xs text-muted mb-1"><span>{j.step}</span><span>{Math.round(j.progress)}%</span></div><ProgressBar value={j.progress} /></div>}
                {j.error && <p className="text-xs text-danger mt-2 break-words">{j.error}</p>}
              </div>))}</div>)}
        </>)}
        {tab === 'config' && s && <Config s={s} onSaved={sys.reload} />}
      </div>
      {open && <LogModal id={open} onClose={() => setOpen(null)} />}
    </>
  )
}

function Meter({ label: l, value, sub, icon }: { label: string; value: number; sub: string; icon: React.ReactNode }) {
  return <div className="card p-4"><div className="flex items-center gap-2 text-xs text-muted mb-2">{icon}{l}</div><p className="font-display text-2xl mb-1">{Math.round(value)}%</p><ProgressBar value={value} /><p className="text-xs text-muted mt-1.5">{sub}</p></div>
}
const Dot = ({ ok, warn }: { ok: boolean; warn?: boolean }) => ok ? <CheckCircle2 size={16} className="text-ok" /> : warn ? <Zap size={16} className="text-warn" /> : <XCircle size={16} className="text-danger" />

function Status({ s }: { s: any }) {
  const g = s.gpu.gpus?.[0]
  return (
    <div className="space-y-4">
      <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-3">
        <Meter label="CPU" value={s.cpu.percent} sub={`${s.cpu.cores} logical cores`} icon={<Cpu size={14} />} />
        <Meter label="System RAM" value={s.ram.percent} sub={`${s.ram.used_gb} / ${s.ram.total_gb} GB used · ${s.ram.available_gb} GB free`} icon={<MemoryStick size={14} />} />
        {g ? <Meter label={`GPU VRAM · ${g.name}`} value={(g.vram_used_mb / g.vram_total_mb) * 100} sub={`${g.vram_used_mb} / ${g.vram_total_mb} MB · GPU load ${g.utilization}% · ${g.temperature_c}°C`} icon={<MonitorCog size={14} />} />
          : <div className="card p-4"><div className="flex items-center gap-2 text-xs text-muted mb-2"><MonitorCog size={14} />GPU</div><p className="text-sm">No NVIDIA GPU detected</p><p className="text-xs text-muted mt-1">Install the NVIDIA driver (nvidia-smi) to enable GPU processing; CPU is used meanwhile.</p></div>}
        <Meter label="Disk" value={s.disk.used_percent} sub={`${s.disk.free_gb} GB free of ${s.disk.total_gb} GB`} icon={<HardDrive size={14} />} />
      </div>
      <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-3 text-sm">
        <div className="card p-4 space-y-1"><p className="flex items-center gap-2 font-medium"><Dot ok={s.whisper.installed} />Whisper</p>
          <p className="text-muted">Model: <b className="text-ink">{s.whisper.model}</b> on <b className="text-ink">{s.whisper.device === 'cuda' ? `GPU (${s.whisper.compute_type === 'auto' ? 'float16' : s.whisper.compute_type})` : 'CPU'}</b>{s.whisper.cuda_available ? '' : ' · no CUDA'}</p><p className="text-xs text-muted">{s.whisper.status}</p></div>
        <div className="card p-4 space-y-1"><p className="flex items-center gap-2 font-medium"><Dot ok={s.ollama.connected} />Ollama</p>
          <p className="text-muted">Planner: <b className="text-ink">{s.ollama.effective ?? s.active_llm}</b>{s.ollama.effective && s.ollama.effective !== s.active_llm ? ' (fallback)' : ''}</p>
          <p className="text-xs text-muted">{s.ollama.connected ? (s.ollama.active_installed ? 'Connected - model installed' : s.ollama.fallback_installed ? `Connected - “${s.active_llm}” is not installed, using “${s.fallback_llm}”. Run “ollama pull ${s.active_llm}”.` : `Connected - run “ollama pull ${s.active_llm}”`) : 'Not running. Start Ollama to enable content planning (transcription still works).'}</p></div>
        <div className="card p-4 space-y-1"><p className="flex items-center gap-2 font-medium"><Dot ok={s.ffmpeg.available} />FFmpeg</p><p className="text-muted">{s.ffmpeg.available ? 'Available' : 'Not found on PATH'}</p><p className="text-xs text-muted">NVENC GPU encoding: {s.ffmpeg.nvenc ? 'available' : 'not available'}</p></div>
        <div className="card p-4 space-y-1"><p className="flex items-center gap-2 font-medium"><Dot ok={s.translator.available} warn />El Ruach Translator</p><p className="text-muted">{s.translator.available ? 'Linked' : 'Not linked'}</p>
          <p className="text-xs text-muted break-all">{s.translator.available ? s.translator.directory : s.translator.reason}</p></div>
      </div>
      <p className="text-xs text-muted flex flex-wrap gap-x-4"><span>Model loading: {s.model_loading.policy}</span><span>Embedding: {s.embedding.active ?? 'n/a'}</span>{s.model_loading.active_job_id && <span className="text-accent">Running job #{s.model_loading.active_job_id}</span>}</p>
    </div>)
}

function Config({ s, onSaved }: { s: any; onSaved: () => void }) {
  const toast = useToast()
  const { data, reload } = useApi(() => api.get<{ values: Record<string, any> }>('/api/settings'), [])
  const [v, setV] = useState<Record<string, any>>({})
  const [busy, setBusy] = useState(false)
  const cur = { ...(data?.values ?? {}), ...v }
  const set = (k: string, val: any) => setV({ ...v, [k]: val })
  const style = { ...(cur['subtitle.style'] ?? {}) }
  const setStyle = (k: string, val: any) => set('subtitle.style', { ...style, [k]: val })
  const save = async () => { setBusy(true); try { await api.put('/api/settings', { values: v }); toast.ok('Settings saved - applied to the next job'); setV({}); reload(); onSaved() } catch (e) { toast.err(e) } finally { setBusy(false) } }
  if (!data) return <Loading />
  const sel = (k: string, opts: string[]) => <select className="input" value={cur[k]} onChange={(e) => set(k, e.target.value)}>{opts.map((o) => <option key={o}>{o}</option>)}</select>
  return (
    <div className="grid lg:grid-cols-2 gap-5">
      <div className="card p-4 space-y-3"><h2 className="font-display text-lg">Models</h2>
        <Field label="Whisper model (GPU)" hint="Used when an NVIDIA GPU is available (CUDA). “large-v3-turbo” is the most accurate and fits an RTX 3060 12 GB.">{sel('whisper.model', s.whisper_models)}</Field>
        <Field label="Whisper model (CPU fallback)" hint="Used instead when there is no GPU, or the GPU model cannot be loaded. “small” is the usual choice.">{sel('whisper.cpu_model', s.whisper_models)}</Field>
        <Field label="Whisper precision on the GPU" hint="float16 is the default and best for an RTX 3060. The CPU always uses int8.">{sel('whisper.compute_type', ['auto', 'float16', 'int8_float16', 'int8', 'float32'])}</Field>
        <Field label="Spoken language of the videos" hint="Tells Whisper which language to transcribe. Keep English for English sermons; “Auto-detect” can guess wrong on accented speech.">
          <select className="input" value={cur['whisper.language'] ?? 'en'} onChange={(e) => set('whisper.language', e.target.value)}>
            {(s.whisper_languages ?? [{ code: 'en', name: 'English' }]).map((l: { code: string; name: string }) => <option key={l.code} value={l.code}>{l.name}</option>)}</select></Field>
        <Field label="Planner model (Ollama)" hint="Plans the reels and writes hooks. Default qwen2.5:7b - run “ollama pull qwen2.5:7b” once."><input className="input" list="llms" value={cur['llm.model']} onChange={(e) => set('llm.model', e.target.value)} /><datalist id="llms">{(s.ollama.models ?? []).map((m: string) => <option key={m} value={m} />)}</datalist></Field>
        <Field label="Planner fallback model" hint="Used automatically when the planner model is not installed or does not fit in memory. Leave empty to turn the fallback off."><input className="input" list="llms" value={cur['llm.fallback_model'] ?? ''} onChange={(e) => set('llm.fallback_model', e.target.value)} /></Field>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!cur['hook.one_word']} onChange={(e) => set('hook.one_word', e.target.checked)} />Use a single bold word as the reel hook (default off: a 3-8 word phrase from the sermon)</label>
        <Field label="Processing device"><select className="input" value={cur['gpu.mode']} onChange={(e) => set('gpu.mode', e.target.value)}><option value="auto">Automatic (GPU when available)</option><option value="gpu">Force GPU</option><option value="cpu">CPU only</option></select></Field>
        <Field label="Minimum free RAM before loading a model (GB)" hint="Minimum 1 GB (default 3 GB). Jobs wait/fail with a clear message instead of freezing the PC."><input className="input" type="number" step="0.5" min="1" value={cur['gpu.min_free_ram_gb']} onChange={(e) => set('gpu.min_free_ram_gb', Number(e.target.value))} /></Field>
      </div>
      <div className="card p-4 space-y-3"><h2 className="font-display text-lg">Output</h2>
        <Field label="Caption style" hint={`${(s.caption_templates ?? []).find((t: any) => t.id === (cur['subtitle.template'] ?? 'stacked'))?.note ?? ''} A project can choose its own when you create its reels. Indian-language captions use a Noto Sans font for their script.`}>
          <select className="input" value={cur['subtitle.template'] ?? 'stacked'} onChange={(e) => set('subtitle.template', e.target.value)}>
            {(s.caption_templates ?? []).map((t: any) => <option key={t.id} value={t.id}>{t.label}</option>)}</select></Field>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={cur['whisper.word_timestamps'] !== false} onChange={(e) => set('whisper.word_timestamps', e.target.checked)} />Time every spoken word when transcribing (needed for Word pop and Karaoke to match the speech exactly; applies to new transcripts)</label>
        <Field label="Video formats for each reel" hint="Tick one or more: every reel is made in each. The first ticked (9:16 if ticked) is the main file; the others are saved beside it and shown as a format switch on the reel.">
          <div className="flex flex-wrap gap-x-4 gap-y-1">{(s.video_formats ?? [['9:16', 'Vertical 9:16 (Reels, Shorts, TikTok)'], ['4:5', 'Portrait 4:5 (Instagram feed)'], ['1:1', 'Square 1:1'], ['16:9', 'Landscape 16:9 (YouTube)']]).map(([id, name]: [string, string]) => {
            const cur_ = (cur['reel.formats'] ?? ['9:16']) as string[]
            return <label key={id} className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={cur_.includes(id)}
              onChange={(e) => { const next = e.target.checked ? [...cur_, id] : cur_.filter((x) => x !== id); if (next.length) set('reel.formats', next) }} />{name}</label>
          })}</div></Field>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={cur['reel.face_tracking'] !== false} onChange={(e) => set('reel.face_tracking', e.target.checked)} />Follow the speaker's face (stretches with no face get a blurred background with the whole frame centred)</label>
        <Field label="Face detector" hint={`${s.reel_video?.face_detector?.active ? `Working here: ${s.reel_video.face_detector.active}. ` : 'No face detector could start here - reels use a centre crop. '}Video is encoded with ${s.reel_video?.encoder ?? 'libx264'} (NVENC when the GPU allows it) at -14 LUFS.`}>
          <select className="input" value={cur['reel.face_detector'] ?? 'auto'} onChange={(e) => set('reel.face_detector', e.target.value)}>
            <option value="auto">Automatic (MediaPipe, plus OpenCV when it misses faces)</option><option value="mediapipe">MediaPipe only</option><option value="opencv">OpenCV (YuNet) only</option></select></Field>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={cur['reels.unique_copy'] !== false} onChange={(e) => set('reels.unique_copy', e.target.checked)} />Write each reel's caption from its own transcript, and rewrite one that is too close to an earlier caption (one extra AI prompt per reel)</label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={cur['thumbnail.best_frame'] !== false} onChange={(e) => set('thumbnail.best_frame', e.target.checked)} />Pick thumbnails by face size, open eyes and sharpness instead of a fixed frame</label>
        <div className="grid grid-cols-2 gap-3"><Field label="Designed thumbnails per sermon" hint="1280×720, with the clip's hook and your brand kit."><select className="input" value={cur['thumbnail.variations'] ?? 3} onChange={(e) => set('thumbnail.variations', Number(e.target.value))}>{[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}</select></Field>
          <Field label="Carousel slides" hint="Cover + main points + closing."><select className="input" value={cur['carousel.slides'] ?? 6} onChange={(e) => set('carousel.slides', Number(e.target.value))}>{[5, 6, 7].map((n) => <option key={n} value={n}>{n}</option>)}</select></Field></div>
        <Field label="Short reels per sermon" hint="Plus one long main reel. The AI proposes twice as many clips, scores each 0-100 and keeps the best ones that do not overlap. A short video may fit fewer.">
          <input className="input" type="number" min={1} max={12} value={cur['reels.count']} onChange={(e) => set('reels.count', Math.round(Number(e.target.value)))} /></Field>
        <div className="grid grid-cols-2 gap-3"><Field label="Shortest reel (s)"><input className="input" type="number" min={10} max={170} value={cur['reels.min_s']} onChange={(e) => set('reels.min_s', Number(e.target.value))} /></Field>
          <Field label="Longest reel (s)" hint="At least 5 s more than the shortest."><input className="input" type="number" min={15} max={180} value={cur['reels.max_s']} onChange={(e) => set('reels.max_s', Number(e.target.value))} /></Field></div>
        <Field label="Processing quality">{sel('render.quality', ['draft', 'balanced', 'high'])}</Field>
        <div className="grid grid-cols-2 gap-3"><Field label="Reel width"><input className="input" type="number" value={cur['render.width']} onChange={(e) => set('render.width', Number(e.target.value))} /></Field>
          <Field label="Reel height"><input className="input" type="number" value={cur['render.height']} onChange={(e) => set('render.height', Number(e.target.value))} /></Field></div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!cur['render.burn_subtitles']} onChange={(e) => set('render.burn_subtitles', e.target.checked)} />Burn subtitles into reels (an editable .srt is always saved too)</label>
        <div className="grid grid-cols-2 gap-3"><Field label="Subtitle font"><input className="input" value={style.font ?? ''} onChange={(e) => setStyle('font', e.target.value)} /></Field>
          <Field label="Font size (px at output height)"><input className="input" type="number" value={style.size ?? 64} onChange={(e) => setStyle('size', Number(e.target.value))} /></Field>
          <Field label="Text colour"><input className="input !p-1 h-10" type="color" value={style.primary ?? '#FFFFFF'} onChange={(e) => setStyle('primary', e.target.value)} /></Field>
          <Field label="Outline colour"><input className="input !p-1 h-10" type="color" value={style.outline_color ?? '#000000'} onChange={(e) => setStyle('outline_color', e.target.value)} /></Field>
          <Field label="Bottom margin"><input className="input" type="number" value={style.margin_v ?? 260} onChange={(e) => setStyle('margin_v', Number(e.target.value))} /></Field>
          <Field label="Max characters per line"><input className="input" type="number" value={style.max_chars_per_line ?? 28} onChange={(e) => setStyle('max_chars_per_line', Number(e.target.value))} /></Field></div>
      </div>
      <div className="lg:col-span-2 flex justify-end"><button className="btn-primary" onClick={save} disabled={busy || !Object.keys(v).length}>{busy ? <Spinner /> : <Save size={15} />}Save changes</button></div>
    </div>)
}

function LogModal({ id, onClose }: { id: number; onClose: () => void }) {
  const { data, loading } = useApi(() => api.get<Job>(`/api/jobs/${id}`), [id], 2000)
  return (
    <Modal title={`Job #${id} ${data ? '· ' + label(data.job_type) : ''}`} onClose={onClose} width="max-w-3xl">
      {loading && !data ? <Loading rows={2} /> : data && (<>
        <div className="flex gap-2 items-center mb-3"><StatusBadge status={data.status} /><span className="text-xs text-muted">started {fmtDate(data.started_at)} · finished {fmtDate(data.completed_at)}</span></div>
        {data.error && <p className="text-sm text-danger mb-3 break-words">{data.error}</p>}
        <div className="bg-bg/70 rounded-xl border border-line/70 p-3 max-h-[50vh] overflow-auto font-mono text-xs space-y-1">
          {data.logs?.map((l) => <p key={l.id} className={l.level === 'ERROR' ? 'text-danger' : l.level === 'WARNING' ? 'text-warn' : 'text-muted'}><span className="opacity-60">{new Date(l.at + 'Z').toLocaleTimeString()}</span> {l.message}</p>)}</div>
      </>)}
    </Modal>)
}
