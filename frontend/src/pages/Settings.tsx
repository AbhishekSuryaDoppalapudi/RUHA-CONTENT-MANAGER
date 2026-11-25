import { useRef, useState } from 'react'
import { Download, RotateCcw, Save, Trash2, Upload } from 'lucide-react'
import { api, LANGUAGES } from '../api'
import { Empty, ErrorBox, Field, Loading, PageHeader, Spinner, Tabs, fmtBytes, fmtDate, useApi, useConfirm, useToast } from '../ui'

export default function SettingsPage() {
  const [tab, setTab] = useState('app')
  return (
    <>
      <PageHeader title="Settings" subtitle="Application, storage, privacy, backups and updates." />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'app', label: 'Application' }, { id: 'ai', label: 'AI & GPU' }, { id: 'export', label: 'Export & subtitles' }, { id: 'creative', label: 'Creative Studio' }, { id: 'storage', label: 'Storage & database' },
        { id: 'privacy', label: 'Privacy & logging' }, { id: 'backup', label: 'Backup & restore' }, { id: 'updates', label: 'Updates' }]} />
      <div className="mt-5">{tab === 'backup' ? <Backup /> : tab === 'storage' ? <Storage /> : tab === 'updates' ? <Updates /> : <Values group={tab} />}</div>
    </>)
}

const GROUPS: Record<string, { title: string; fields: [string, string, 'text' | 'number' | 'bool' | ['select', string[]], string?][] }[]> = {
  app: [{ title: 'Application', fields: [['app.owner_name', 'Your name (shown on approvals)', 'text', 'Recorded as the reviewer. The app is single-user for now.'], ['app.theme', 'Default theme', ['select', ['dark', 'light']]], ['storage.warn_free_gb', 'Warn when free disk is below (GB)', 'number']] }],
  ai: [{ title: 'AI models', fields: [['whisper.model', 'Whisper model', ['select', ['tiny', 'base', 'small', 'medium', 'large-v3', 'turbo']]], ['whisper.device', 'Whisper device', ['select', ['auto', 'cuda', 'cpu']]], ['llm.model', 'Ollama LLM', 'text'], ['llm.base_url', 'Ollama address (local only)', 'text'], ['llm.keep_alive', 'Keep LLM loaded for', 'text', 'e.g. 2m, or 0 to unload immediately'], ['llm.temperature', 'LLM temperature', 'number'], ['embedding.provider', 'Embeddings', ['select', ['auto', 'ollama', 'hash']], 'auto uses an Ollama embedding model when present, otherwise an offline lexical embedding.'], ['embedding.model', 'Ollama embedding model', 'text']] },
    { title: 'GPU / memory', fields: [['gpu.mode', 'Processing device', ['select', ['auto', 'gpu', 'cpu']]], ['gpu.min_free_ram_gb', 'Minimum free RAM to load a model (GB)', 'number']] }],
  export: [{ title: 'Video export', fields: [['render.quality', 'Quality', ['select', ['draft', 'balanced', 'high']]], ['render.width', 'Reel width', 'number'], ['render.height', 'Reel height', 'number'], ['render.burn_subtitles', 'Burn subtitles into reels', 'bool'], ['render.apply_watermark', 'Apply brand watermark', 'bool'], ['render.apply_intro_outro', 'Add brand intro/outro', 'bool'], ['reel.music_path', 'Music file for image reels (optional, full path)', 'text', 'An mp3/wav/m4a played under reels made from images. Leave empty for a soft built-in ambient sound.'], ['reel.hook_card', 'Opening hook image on reels', 'bool'], ['reel.closing_card', 'Closing image on reels', 'bool'], ['subtitle.language', 'Default subtitle language', ['select', LANGUAGES]], ['design.theme_backgrounds', 'Theme-matched poster and post backgrounds', 'bool'], ['design.engine', 'Poster renderer', ['select', ['html', 'pillow']], 'html uses your installed Edge/Chrome for the best typography (and correct Indian-language text). pillow is the built-in fallback.'], ['design.ai_background', 'AI-generated backgrounds (needs NVIDIA GPU)', 'bool', 'Paints a sermon-specific background with Stable Diffusion. Install requirements-gpu.txt first; falls back to the built-in background if unavailable.'], ['design.ai_model', 'AI background model', 'text']] }],
  creative: [{ title: 'Image generation', fields: [['creative.backend', 'Back-end', ['select', ['auto', 'diffusers', 'comfyui', 'none']], 'auto uses a running ComfyUI if found, else local Stable Diffusion (needs requirements-gpu.txt and an NVIDIA GPU).'], ['creative.model', 'Model', ['select', ['auto', 'sd15', 'sdxl', 'sdxl-turbo']], 'auto picks from your GPU memory (SDXL needs ~10 GB; SD 1.5 for 4-8 GB). SDXL-Turbo is non-commercial only.'], ['creative.comfyui_url', 'ComfyUI address', 'text'], ['creative.comfy_checkpoint', 'ComfyUI checkpoint (blank = first installed)', 'text'], ['creative.steps', 'Sampling steps (0 = model default)', 'number'], ['creative.allow_cpu', 'Allow CPU image generation (very slow)', 'bool'], ['creative.max_attempts', 'Automatic retries when a quality check fails', 'number'], ['creative.show_brand', 'Show the El Ruach lockup on creatives', 'bool']] }],
  privacy: [{ title: 'Privacy', fields: [['privacy.allow_network', 'Allow optional network access', 'bool', 'Only used to check for application updates. All AI runs locally either way. Nothing is ever uploaded automatically.']] }, { title: 'Logging', fields: [['logging.level', 'Log level', ['select', ['DEBUG', 'INFO', 'WARNING', 'ERROR']], 'Takes effect after restart.']] }],
}

function Values({ group }: { group: string }) {
  const toast = useToast()
  const { data, error, loading, reload } = useApi(() => api.get<{ values: Record<string, any> }>('/api/settings'), [])
  const [v, setV] = useState<Record<string, any>>({}); const [busy, setBusy] = useState(false)
  const logs = useApi(() => api.get<{ lines: string[] }>('/api/system/logs?lines=60'), [group], group === 'privacy' ? 5000 : 0)
  if (loading && !data) return <Loading />
  if (error) return <ErrorBox error={error} retry={reload} />
  const cur = { ...data!.values, ...v }
  const save = async () => { setBusy(true); try { await api.put('/api/settings', { values: v }); toast.ok('Settings saved'); if (v['app.theme']) { document.documentElement.setAttribute('data-theme', v['app.theme']); localStorage.setItem('ruha-theme', v['app.theme']) } setV({}); reload() } catch (e) { toast.err(e) } finally { setBusy(false) } }
  return (
    <div className="space-y-5 max-w-3xl">
      {(GROUPS[group] ?? []).map((g) => (
        <section key={g.title} className="card p-4 space-y-3"><h2 className="font-display text-lg">{g.title}</h2>
          {g.fields.map(([k, l, t, hint]) => (
            <Field key={k} label={l} hint={hint}>{t === 'bool' ? <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!cur[k]} onChange={(e) => setV({ ...v, [k]: e.target.checked })} />Enabled</label>
              : Array.isArray(t) ? <select className="input" value={cur[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })}>{t[1].map((o) => <option key={o}>{o}</option>)}</select>
              : <input className="input" type={t === 'number' ? 'number' : 'text'} step="any" value={cur[k] ?? ''} onChange={(e) => setV({ ...v, [k]: t === 'number' ? Number(e.target.value) : e.target.value })} />}</Field>))}</section>))}
      <button className="btn-primary" onClick={save} disabled={busy || !Object.keys(v).length}>{busy ? <Spinner /> : <Save size={15} />}Save</button>
      {group === 'privacy' && <section className="card p-4"><h2 className="font-display text-lg mb-2">Recent log</h2>
        <pre className="text-xs text-muted max-h-72 overflow-auto whitespace-pre-wrap">{logs.data?.lines.join('\n') || 'Log is empty.'}</pre><p className="text-xs text-muted mt-2">Personal folder names and tokens are stripped from log lines.</p></section>}
    </div>)
}

function Storage() {
  const { data, error, loading, reload } = useApi(() => api.get<any>('/api/system/info'), [])
  if (loading && !data) return <Loading />
  if (error) return <ErrorBox error={error} retry={reload} />
  const d = data
  const rows: [string, string][] = [['Application version', d.version], ['Database schema version', String(d.schema_version)], ['Database file', d.database.path], ['Database size', fmtBytes(d.database.size_bytes)],
    ['Records', `${d.database.projects} projects · ${d.database.videos} videos · ${d.database.assets} assets · ${d.database.jobs} jobs`], ['Data folder', d.storage.data_dir], ['Project workspaces', d.storage.projects_dir], ['Brand files', d.storage.brand_dir], ['Backups', d.storage.backups_dir],
    ['Vector store (rebuildable)', d.storage.chroma_dir], ['Logs', d.storage.logs_dir], ['Maximum upload', `${d.limits.max_upload_gb} GB`], ['Output formats', Object.values(d.output_formats).join(' · ')]]
  return (
    <div className="card p-4 max-w-4xl"><dl className="divide-y divide-line/50">{rows.map(([k, v]) => <div key={k} className="grid sm:grid-cols-[220px_1fr] gap-1 py-2 text-sm"><dt className="text-muted">{k}</dt><dd className="break-all">{v}</dd></div>)}</dl>
      <p className="text-xs text-muted mt-3">Storage locations are set with <code>RUHA_DATA_DIR</code> in the <code>.env</code> file (restart required). Files outside this folder are never read or served.</p></div>)
}

function Backup() {
  const toast = useToast(), confirm = useConfirm()
  const { data, loading, reload } = useApi(() => api.get<any[]>('/api/backups'), [])
  const [media, setMedia] = useState(true); const [busy, setBusy] = useState(''); const up = useRef<HTMLInputElement>(null)
  const run = async (n: string, fn: () => Promise<any>, ok: (r: any) => string) => { setBusy(n); try { toast.ok(ok(await fn())); reload() } catch (e) { toast.err(e) } finally { setBusy('') } }
  return (
    <div className="space-y-5 max-w-3xl">
      <section className="card p-4 space-y-3"><h2 className="font-display text-lg">Create a backup</h2>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={media} onChange={(e) => setMedia(e.target.checked)} />Include videos and media (large). Untick for a small database-and-text backup.</label>
        <div className="flex gap-2 flex-wrap"><button className="btn-primary" disabled={!!busy} onClick={() => run('create', () => api.post('/api/backups', { include_media: media }), (r) => `Backup created (${fmtBytes(r.size_bytes)})`)}>{busy === 'create' ? <Spinner /> : <Save size={15} />}Back up now</button>
          <button className="btn-ghost" onClick={() => up.current?.click()}><Upload size={15} />Import a backup file</button>
          <input ref={up} type="file" accept=".zip" hidden onChange={async (e) => { const f = e.target.files?.[0]; if (!f) return; const fd = new FormData(); fd.append('file', f); await run('up', () => api.upload('/api/backups/upload', fd), () => 'Backup imported - restore it from the list'); e.target.value = '' }} /></div>
        <p className="text-xs text-muted">The AI-memory vectors are rebuilt automatically from stored text after a restore.</p></section>
      <section className="card p-4"><h2 className="font-display text-lg mb-3">Backups</h2>
        {loading ? <Loading rows={1} /> : !data?.length ? <Empty title="No backups yet" /> : <ul className="divide-y divide-line/50">{data.map((b) => (
          <li key={b.name} className="py-2.5 flex items-center gap-3 flex-wrap text-sm"><div className="flex-1 min-w-0"><p className="truncate">{b.name}</p><p className="text-xs text-muted">{fmtDate(b.created_at)} · {fmtBytes(b.size_bytes)}</p></div>
            <a className="btn-ghost !py-1" href={`/api/backups/${b.name}/download`}><Download size={14} />Download</a>
            <button className="btn-ghost !py-1" disabled={!!busy} onClick={async () => { if (await confirm({ title: 'Restore this backup?', body: 'Current data is replaced by the backup. A safety backup of the current state is made first.', confirm: 'Restore' })) run('restore', () => api.post<any>(`/api/backups/${b.name}/restore`), (r) => `Restored. Safety backup: ${r.safety_backup}`) }}><RotateCcw size={14} />Restore</button>
            <button className="btn-danger !py-1" aria-label="Delete backup" onClick={async () => { if (await confirm({ title: 'Delete this backup file?', confirm: 'Delete', danger: true })) run('del', () => api.del(`/api/backups/${b.name}`), () => 'Backup deleted') }}><Trash2 size={14} /></button></li>))}</ul>}</section>
    </div>)
}

function Updates() {
  const { data, loading, error, reload } = useApi(() => api.get<any>('/api/system/updates'), [])
  if (loading && !data) return <Loading rows={1} />
  if (error) return <ErrorBox error={error} retry={reload} />
  return (
    <div className="card p-4 max-w-2xl space-y-2 text-sm"><p>El Ruach Manager <b>{data.version}</b></p>
      {data.git && <p className="text-muted">Git branch <b>{data.git.branch}</b> @ {data.git.commit}{data.git.dirty ? ' (local changes)' : ''}</p>}
      <p>Update check: <b>{data.check}</b></p>
      <div><p className="text-muted mb-1">To update:</p><ol className="list-decimal ml-5 text-muted">{data.instructions.map((i: string) => <li key={i}>{i}</li>)}</ol></div>
      <p className="text-xs text-muted">Updates are never downloaded automatically.</p></div>)
}
