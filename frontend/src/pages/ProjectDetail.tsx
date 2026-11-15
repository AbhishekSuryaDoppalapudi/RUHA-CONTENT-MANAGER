import { useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Image as ImageIcon, Languages, Play, Replace, RotateCw, Save, Sparkles, Trash2, UploadCloud } from 'lucide-react'
import { api, Asset, fileUrl, Job, Project } from '../api'
import { Empty, ErrorBox, Field, Loading, PageHeader, ProgressBar, Spinner, StatusBadge, Tabs, ago, fmtBytes, fmtDate, fmtDur, label, useApi, useConfirm, useDebounced, useToast, PROJECT_STATUS_OPTIONS } from '../ui'
import AssetCard from '../components/AssetCard'
import AssetDetail from '../components/AssetDetail'
import { ImageReelModal, RunJobModal } from '../components/Dialogs'

export default function ProjectDetail() {
  const { id } = useParams()
  const pid = Number(id)
  const nav = useNavigate(), toast = useToast(), confirm = useConfirm()
  const [tab, setTab] = useState('overview')
  const [open, setOpen] = useState<number | null>(null)
  const [modal, setModal] = useState(false)
  const [imgModal, setImgModal] = useState(false)
  const { data: p, error, loading, reload } = useApi(() => api.get<Project>(`/api/projects/${pid}`), [pid], 3000)
  const { data: assets, reload: reloadAssets } = useApi(() => api.get<Asset[]>(`/api/assets?project_id=${pid}`), [pid, p?.updated_at, p?.status], 0)
  const [edit, setEdit] = useState<{ name: string; description: string; tags: string; content_type: string } | null>(null)
  const [pct, setPct] = useState<number | null>(null)
  const file = useRef<HTMLInputElement>(null)
  const [replace, setReplace] = useState(false)

  if (loading && !p) return <Loading rows={3} />
  if (error && !p) return <ErrorBox error={error} retry={reload} />
  if (!p) return null
  const busy = p.status === 'processing'

  const upload = async (f: File) => {
    const form = new FormData(); form.append('file', f); form.append('replace', String(replace))
    setPct(0)
    try {
      const r = await api.upload<{ duplicates: { project_name: string }[] }>(`/api/projects/${pid}/videos`, form, setPct)
      toast.ok('Video uploaded and analysed')
      if (r.duplicates.length) toast.info(`Same file already exists in: ${r.duplicates.map((d) => d.project_name).join(', ')}`)
      reload()
    } catch (e) { toast.err(e) } finally { setPct(null); setReplace(false); if (file.current) file.current.value = '' }
  }
  const act = async (fn: () => Promise<any>, ok: string) => { try { await fn(); toast.ok(ok); reload(); reloadAssets() } catch (e) { toast.err(e) } }
  const startEdit = () => setEdit({ name: p.name, description: p.description, tags: p.tags.join(', '), content_type: p.content_type })
  const group = (kinds: string[]) => (assets ?? []).filter((a) => kinds.includes(a.kind))
  const sections: [string, string[]][] = [['Main reels', ['main_reel']], ['Short reels', ['short_reel']], ['Reels from images', ['image_reel']], ['Highlight poster', ['poster']], ['Encouragement posts', ['social_post']],
    ['Translations & dubbing', ['translation_video', 'translation_subtitle', 'audio']], ['Transcripts & subtitles', ['transcript', 'subtitle', 'caption']]]
  const total = assets?.length ?? 0

  return (
    <>
      <PageHeader title={p.name} subtitle={`${label(p.content_type)} · created ${fmtDate(p.created_at)}`}
        actions={<>
          <StatusBadge status={p.status} />
          <button className="btn-primary" disabled={!p.video || busy} onClick={() => act(() => api.post(`/api/projects/${pid}/process`, {}), 'Pipeline queued')}><Sparkles size={16} />{busy ? 'Processing…' : total ? 'Run pipeline again' : 'Generate content'}</button>
          <button className="btn-ghost" disabled={!p.video} onClick={() => setModal(true)}><Languages size={16} />Translate / dub</button>
          <button className="btn-ghost" onClick={() => setImgModal(true)}><ImageIcon size={16} />Reel from images</button>
          <Link className="btn-ghost" to={`/review?project=${pid}`}>Review this project</Link>
          <Link className="btn-ghost" to={`/library?project=${pid}`}>Open in library</Link>
        </>} />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'overview', label: 'Overview' }, { id: 'assets', label: 'Generated content', count: total }, { id: 'transcript', label: 'Transcript' },
        { id: 'jobs', label: 'Jobs', count: p.jobs?.length }, { id: 'history', label: 'History' }]} />
      <div className="mt-5">
        {tab === 'overview' && (
          <div className="grid lg:grid-cols-[3fr_2fr] gap-5">
            <div className="space-y-4">
              {p.video ? (
                <div className="card overflow-hidden">
                  <video key={p.video.id} controls preload="metadata" className="w-full max-h-[60vh] bg-black" src={fileUrl.video(p.video.id)} poster={p.video.has_thumbnail ? fileUrl.videoThumb(p.video.id) : undefined} />
                  <div className="p-4 grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
                    {[['Duration', fmtDur(p.video.duration)], ['Resolution', `${p.video.width}×${p.video.height}`], ['Size', fmtBytes(p.video.size_bytes)], ['Frame rate', `${p.video.fps} fps`],
                      ['Video codec', p.video.video_codec ?? '-'], ['Audio', p.video.audio_tracks ? `${p.video.audio_tracks} track · ${p.video.audio_codec}` : 'No audio track'], ['File', p.video.original_name], ['SHA-256', p.video.sha256.slice(0, 12) + '…']].map(([k, v]) => (
                      <div key={k}><p className="text-xs text-muted">{k}</p><p className="truncate" title={String(v)}>{v}</p></div>))}
                  </div>
                  {p.video.audio_tracks === 0 && <p className="mx-4 mb-4 text-sm text-warn bg-warn/10 border border-warn/30 rounded-xl p-2">This video has no audio, so it cannot be transcribed.</p>}
                  <div className="px-4 pb-4 flex flex-wrap gap-2">
                    <button className="btn-ghost" onClick={() => { setReplace(true); file.current?.click() }}><Replace size={15} />Replace video</button>
                    <button className="btn-danger" onClick={async () => { if (await confirm({ title: 'Delete the source video?', body: 'The video file is removed from disk. Generated content is kept.', confirm: 'Delete video', danger: true })) act(() => api.del(`/api/videos/${p.video!.id}?confirm=true`), 'Video deleted') }}><Trash2 size={15} />Delete video</button>
                    <a className="btn-ghost" href={`/api/videos/${p.video.id}/download`}>Download original</a>
                  </div>
                </div>
              ) : (
                <button onClick={() => file.current?.click()} className="card border-dashed w-full py-14 flex flex-col items-center gap-2 text-muted hover:border-accent/60 transition">
                  <UploadCloud size={36} /><span className="text-ink font-display text-lg">Upload a video</span><span className="text-sm">MP4, MOV, MKV or AVI · the original is never modified</span></button>)}
              <input ref={file} type="file" hidden accept=".mp4,.mov,.mkv,.avi,.m4v,.webm,video/*" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
              {pct !== null && <div className="card p-4"><p className="text-sm mb-2">Uploading and analysing… {pct}%</p><ProgressBar value={pct} /></div>}
              {(p.jobs ?? []).filter((j) => j.status === 'running' || j.status === 'queued').map((j) => <JobRow key={j.id} j={j} />)}
            </div>

            <div className="card p-4 space-y-3 h-fit">
              {!edit ? (<>
                <div className="flex items-center justify-between"><h2 className="font-display text-lg">Details</h2><button className="btn-ghost" onClick={startEdit}>Edit</button></div>
                <p className="text-sm text-muted whitespace-pre-wrap">{p.description || 'No description'}</p>
                <div className="flex flex-wrap gap-1">{p.tags.length ? p.tags.map((t) => <span key={t} className="chip border-line text-muted">#{t}</span>) : <span className="text-xs text-muted">No tags</span>}</div>
                <Field label="Project status" hint="Set to Approved/Published yourself - approving single assets never changes it automatically.">
                  <select className="input" value={p.status} onChange={(e) => act(() => api.patch(`/api/projects/${pid}`, { status: e.target.value }), 'Status updated')}>
                    {PROJECT_STATUS_OPTIONS.map((s) => <option key={s} value={s}>{label(s)}</option>)}</select></Field>
                <div className="flex gap-2">
                  <button className="btn-ghost" onClick={() => act(async () => { const d = await api.post<Project>(`/api/projects/${pid}/duplicate`); nav(`/projects/${d.id}`) }, 'Duplicated')}>Duplicate</button>
                  <button className="btn-danger ml-auto" onClick={async () => { if (await confirm({ title: `Delete “${p.name}”?`, body: 'Everything in this project is permanently deleted.', confirm: 'Delete forever', danger: true, requireText: p.name })) { await api.del(`/api/projects/${pid}?confirm=true`); toast.ok('Project deleted'); nav('/projects') } }}><Trash2 size={15} />Delete</button>
                </div>
              </>) : (<>
                <Field label="Name"><input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
                <Field label="Description"><textarea className="input" rows={3} value={edit.description} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></Field>
                <Field label="Tags (comma separated)"><input className="input" value={edit.tags} onChange={(e) => setEdit({ ...edit, tags: e.target.value })} /></Field>
                <Field label="Content type"><select className="input" value={edit.content_type} onChange={(e) => setEdit({ ...edit, content_type: e.target.value })}>{['sermon', 'teaching', 'interview', 'event', 'testimony', 'other'].map((t) => <option key={t}>{t}</option>)}</select></Field>
                <div className="flex gap-2 justify-end"><button className="btn-ghost" onClick={() => setEdit(null)}>Cancel</button>
                  <button className="btn-primary" onClick={() => act(async () => { await api.patch(`/api/projects/${pid}`, { ...edit, tags: edit.tags.split(',').map((t) => t.trim()).filter(Boolean) }); setEdit(null) }, 'Saved')}><Save size={15} />Save</button></div>
              </>)}
              {p.translations && p.translations.length > 0 && <div><p className="text-xs uppercase tracking-wider text-muted mb-1">Translations</p>{p.translations.map((t) => (
                <div key={t.id} className="flex items-center gap-2 text-sm py-1"><Languages size={14} className="text-accent" />{t.target}{t.dubbed ? ' (dubbed)' : ' (subtitles)'}<StatusBadge status={t.status} />
                  {t.video_asset_id && <button className="text-accent text-xs hover:underline ml-auto" onClick={() => setOpen(t.video_asset_id)}>Open</button>}</div>))}</div>}
            </div>
          </div>)}

        {tab === 'assets' && (total === 0 ? (
          <Empty icon={<Sparkles size={34} />} title="Nothing generated yet" hint={p.video ? 'Run the pipeline to create reels, a poster and social posts from this video.' : 'Upload a video first.'}
            action={p.video && <button className="btn-primary" onClick={() => act(() => api.post(`/api/projects/${pid}/process`, {}), 'Pipeline queued')}>Generate content</button>} />
        ) : sections.map(([title, kinds]) => { const list = group(kinds); return list.length ? (
          <section key={title} className="mb-8"><h2 className="font-display text-lg mb-3">{title} <span className="text-sm text-muted">({list.length})</span></h2>
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4">{list.map((a) => <AssetCard key={a.id} a={{ ...a, project_name: undefined }} onOpen={() => setOpen(a.id)} />)}</div></section>) : null })) }

        {tab === 'transcript' && <TranscriptTab pid={pid} />}
        {tab === 'jobs' && ((p.jobs?.length ?? 0) === 0 ? <Empty title="No jobs yet" hint="Jobs appear here when you generate content." /> : <div className="space-y-2">{p.jobs!.map((j) => <JobRow key={j.id} j={j} retry={() => act(() => api.post(`/api/jobs/${j.id}/retry`), 'Retry queued')} cancel={() => act(() => api.post(`/api/jobs/${j.id}/cancel`), 'Cancelled')} />)}</div>)}
        {tab === 'history' && <HistoryTab pid={pid} />}
      </div>
      {open && <AssetDetail id={open} onClose={() => setOpen(null)} onChanged={() => { reload(); reloadAssets() }} />}
      {imgModal && <ImageReelModal projectId={pid} onClose={() => setImgModal(false)} onDone={() => { reload(); reloadAssets() }} />}
      {modal && <RunJobModal mode="translate" projectId={pid} onClose={() => { setModal(false); reload() }} />}
    </>
  )
}

export function JobRow({ j, retry, cancel }: { j: Job; retry?: () => void; cancel?: () => void }) {
  return (
    <div className="card p-3">
      <div className="flex items-center gap-3 flex-wrap">
        <b className="text-sm">{label(j.job_type)}</b><StatusBadge status={j.status} /><span className="text-xs text-muted">#{j.id} · {ago(j.created_at)}</span>
        <span className="ml-auto flex gap-2">
          {(j.status === 'failed' || j.status === 'cancelled') && retry && <button className="btn-ghost !py-1" onClick={retry}><RotateCw size={14} />Retry</button>}
          {(j.status === 'queued' || j.status === 'running') && cancel && <button className="btn-ghost !py-1" onClick={cancel}>Cancel</button>}
        </span>
      </div>
      {j.status === 'running' && <div className="mt-2"><div className="flex justify-between text-xs text-muted mb-1"><span>{j.step}</span><span>{Math.round(j.progress)}%</span></div><ProgressBar value={j.progress} /></div>}
      {j.status === 'queued' && j.step && <p className="text-xs text-muted mt-1">{j.step}</p>}
      {j.error && <p className="text-xs text-danger mt-2 break-words">{j.error}</p>}
    </div>
  )
}

function TranscriptTab({ pid }: { pid: number }) {
  const [q, setQ] = useState(''); const dq = useDebounced(q)
  const { data, error, loading } = useApi(() => api.get<any>(`/api/projects/${pid}/transcript${dq ? `?q=${encodeURIComponent(dq)}` : ''}`), [pid, dq])
  if (loading && !data) return <Loading />
  if (error) return <ErrorBox error={error} />
  if (!data?.exists) return <Empty title="No transcript yet" hint="The transcript is created by the transcription step of the pipeline." />
  return (
    <div className="grid lg:grid-cols-[2fr_1fr] gap-5">
      <div className="card p-4">
        <input className="input mb-3" placeholder="Search this transcript…" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="max-h-[60vh] overflow-auto space-y-1 text-sm">{data.segments.length === 0 ? <p className="text-muted">No matches.</p> : data.segments.map((s: any) => (
          <p key={s.idx} className="flex gap-3"><span className="text-xs text-accent tabular-nums w-14 shrink-0 pt-0.5">{fmtDur(s.start)}</span><span>{s.text}</span></p>))}</div>
      </div>
      <div className="card p-4 space-y-3 h-fit text-sm">
        <p className="text-xs text-muted">Language: {data.language} · model {data.model}</p>
        <div><p className="text-xs uppercase tracking-wider text-muted">Summary</p><p>{data.summary || <span className="text-muted">Not generated (the local LLM was unavailable). Re-run the pipeline once Ollama is running.</span>}</p></div>
        {data.themes?.length > 0 && <div><p className="text-xs uppercase tracking-wider text-muted mb-1">Themes</p><div className="flex flex-wrap gap-1">{data.themes.map((t: string) => <span key={t} className="chip border-accent/40 text-accent">{t}</span>)}</div></div>}
        {data.quotes?.length > 0 && <div><p className="text-xs uppercase tracking-wider text-muted mb-1">Verified quotes</p>{data.quotes.map((t: string, i: number) => <p key={i} className="border-l-2 border-primary pl-2 mb-1 text-muted">“{t}”</p>)}</div>}
      </div>
    </div>)
}

function HistoryTab({ pid }: { pid: number }) {
  const { data, loading, error } = useApi(() => api.get<any[]>(`/api/projects/${pid}/history`), [pid])
  if (loading && !data) return <Loading />
  if (error) return <ErrorBox error={error} />
  if (!data?.length) return <Empty title="No history yet" />
  return <div className="card divide-y divide-line/50">{data.map((h) => (
    <div key={h.id} className="flex items-center gap-3 px-4 py-2 text-sm"><span className="chip border-line text-muted">{h.entity}</span><b>{label(h.action.replace(':', ' '))}</b>
      <span className="text-xs text-muted truncate">{Object.entries(h.detail).map(([k, v]) => `${k}: ${v}`).join(' · ')}</span><span className="ml-auto text-xs text-muted shrink-0">{fmtDate(h.at)}</span></div>))}</div>
}
