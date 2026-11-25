import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Check, Copy, Film, Languages, Play, Plus, Save, Scissors, Search, Sparkles, Trash2 } from 'lucide-react'
import { api, DUB_LANGUAGES, fileUrl, Job, LANGUAGES, Project } from '../api'
import { Empty, ErrorBox, Field, Loading, PageHeader, ProgressBar, Spinner, StatusBadge, Tabs, useApi, useConfirm, useToast } from '../ui'
import { addRange, fmt, inReel, previewTick, rangeFromWords, remove, reorder, search, Sentence, Seg, split, total, trimByWord, WARN_SECONDS, Word } from '../reelLogic'

type Options = {
  caption: { enabled: boolean; template: string; font: string; color: string; size: number }
  hook: { enabled: boolean; text: string }
  lower_third: { enabled: boolean; name: string; role: string }
  watermark: { enabled: boolean }
  end_card: { enabled: boolean; text: string }
  music: { path: string; volume: number }
  thumbnail: { time: number | null; text: string; path: string }
  posts: Record<string, string>
}
type Reel = {
  id: number; project_id: number; title: string; status: string; version: number; language: string; aspect: string; source: string
  segments: Seg[]; total_seconds: number; options: Options; caption_edits: Record<string, string>; warnings: string[]
  has_preview: boolean; asset_id: number | null; updated_at: string
}
type Transcript = { sentences: Sentence[]; duration: number; word_times: string }
type Suggestion = { id: string; start: number; end: number; hook: string; reason: string; category: string }

const STEPS = [['draft', 'Draft'], ['ready', 'Ready for review'], ['approved', 'Approved'], ['rendered', 'Rendered']]
const CAPTION_STYLES: [string, string][] = [['wordpop', 'Word pop - current word big, previous word small above'], ['stacked', 'Stacked - small words, one big keyword'],
  ['karaoke', 'Karaoke - the spoken word lights up'], ['classic', 'Classic - plain subtitles at the bottom']]
const PLATFORM_LABEL: Record<string, string> = { instagram: 'Instagram', facebook: 'Facebook', youtube: 'YouTube', x: 'X' }

const WordSpan = memo(function WordSpan({ w, i, picked, on, hit, edited, onDown, onEnter, onEdit }: {
  w: Word; i: number; picked: boolean; on: boolean; hit: boolean; edited?: string; onDown: (i: number) => void; onEnter: (i: number) => void; onEdit: (w: Word) => void
}) {
  const cls = picked ? 'bg-primary/50 text-ink' : on ? 'bg-accent/30 text-ink' : hit ? 'bg-warn/30' : ''
  return <span data-w={i} className={`cursor-pointer rounded px-px ${cls} ${edited ? 'underline decoration-dotted decoration-accent' : ''} ${w.confidence != null && w.confidence < 0.5 ? 'text-warn' : ''}`}
    title={`${fmt(w.start)}${w.confidence != null ? ` · ${Math.round(w.confidence * 100)}% sure` : ''}${edited ? ` · spoken: ${w.w}` : ''}`}
    onMouseDown={(e) => { e.preventDefault(); onDown(i) }} onMouseEnter={() => onEnter(i)} onDoubleClick={() => onEdit(w)}>{edited ?? w.w}{' '}</span>
})

export default function ReelEditor() {
  const { id } = useParams()
  const pid = Number(id)
  const [sp, setSp] = useSearchParams()
  const reelId = Number(sp.get('reel')) || 0
  const { data: proj, error: perr, loading: ploading } = useApi(() => api.get<Project>(`/api/projects/${pid}`), [pid])
  const { data: tr, error: terr, loading: tloading } = useApi(() => api.get<Transcript>(`/api/sermons/${pid}/transcript`), [pid])
  const reels = useApi(() => api.get<{ items: Reel[] }>(`/api/sermons/${pid}/reels`), [pid, reelId])
  const sugg = useApi(() => api.get<{ suggestions: Suggestion[] }>(`/api/sermons/${pid}/suggested-reels`), [pid])
  const toast = useToast()

  if ((ploading && !proj) || (tloading && !tr)) return <Loading rows={3} />
  if (perr && !proj) return <ErrorBox error={perr} />
  if (terr && !tr) return <div><PageHeader title="Reel Editor" /><Empty title="No transcript yet" hint="Run transcription for this sermon first - the editor works from its words." action={<Link className="btn-primary" to={`/projects/${pid}`}>Back to the project</Link>} /></div>
  if (!proj || !tr) return null

  const create = async (body: object) => {
    try { const r = await api.post<Reel>(`/api/sermons/${pid}/reels`, body); setSp({ reel: String(r.id) }) } catch (e) { toast.err(e) }
  }
  if (!reelId) {
    return (
      <div>
        <PageHeader title="Reel Editor" subtitle={proj.name} actions={<>
          <Link className="btn-ghost" to={`/projects/${pid}`}><ArrowLeft size={16} />Back to the project</Link>
          <button className="btn-primary" onClick={() => create({ title: 'New reel' })}><Plus size={16} />New empty reel</button></>} />
        <div className="grid lg:grid-cols-2 gap-5">
          <div className="card p-4"><h2 className="font-medium mb-2">Your reels</h2>
            {reels.data?.items.length ? reels.data.items.map((r) => (
              <button key={r.id} className="w-full text-left flex items-center gap-3 py-2 border-t border-line/60 first:border-0 hover:bg-surface2/50 px-2 rounded" onClick={() => setSp({ reel: String(r.id) })}>
                <Film size={16} /><span className="flex-1 truncate">{r.title}</span><span className="text-xs text-muted">{fmt(r.total_seconds)}</span><StatusBadge status={r.status} /></button>))
              : <p className="text-sm text-muted">No reels yet. Start from a suggestion or an empty reel.</p>}
          </div>
          <div className="card p-4"><h2 className="font-medium mb-1">Suggested by the AI planner</h2><p className="text-xs text-muted mb-2">A starting point only - you can change every part of it.</p>
            {sugg.data?.suggestions.length ? sugg.data.suggestions.map((s) => (
              <div key={s.id} className="flex items-center gap-3 py-2 border-t border-line/60 first:border-0">
                <div className="flex-1 min-w-0"><p className="text-sm truncate">{s.hook || s.id}</p><p className="text-xs text-muted">{fmt(s.start)}-{fmt(s.end)} · {s.category}{s.reason ? ` · ${s.reason}` : ''}</p></div>
                <button className="btn-ghost" onClick={() => create({ suggestion: s.id })}>Edit this</button></div>)) : <p className="text-sm text-muted">No suggestions yet.</p>}
          </div>
        </div>
      </div>
    )
  }
  return <Editor key={reelId} reelId={reelId} proj={proj} tr={tr} back={() => { setSp({}); reels.reload() }} open={(n) => setSp({ reel: String(n) })} gone={() => { setSp({}); reels.reload() }} />
}

function Editor({ reelId, proj, tr, back, open, gone }: { reelId: number; proj: Project; tr: Transcript; back: () => void; open: (id: number) => void; gone: () => void }) {
  const toast = useToast(), confirm = useConfirm()
  const [reel, setReel] = useState<Reel | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [saving, setSaving] = useState(false)
  const [job, setJob] = useState<{ id: number; what: string } | null>(null)
  const [progress, setProgress] = useState(0)
  const [drag, setDrag] = useState<{ a: number; b: number } | null>(null)
  const [phrase, setPhrase] = useState('')
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null)
  const [tab, setTab] = useState('captions')
  const [view, setView] = useState<'source' | 'preview' | 'final'>('source')
  const [playIdx, setPlayIdx] = useState<number | null>(null)
  const [dragSeg, setDragSeg] = useState<number | null>(null)
  const [hooks, setHooks] = useState<string[]>([])
  const [target, setTarget] = useState('Hindi')
  const [dub, setDub] = useState(false)
  const video = useRef<HTMLVideoElement>(null)
  const optTimer = useRef<number | null>(null)
  const words = useMemo(() => tr.sentences.flatMap((s) => s.words), [tr])
  const hits = useMemo(() => new Set(search(tr.sentences, phrase)), [tr, phrase])
  const segs = reel?.segments ?? []

  const load = useCallback(async () => {
    try { setReel(await api.get<Reel>(`/api/editor/reels/${reelId}`)) } catch (e) { setErr(e) }
  }, [reelId])
  useEffect(() => { load() }, [load])

  const save = async (patch: object) => {
    setSaving(true)
    try { setReel(await api.patch<Reel>(`/api/editor/reels/${reelId}`, patch)) } catch (e) { toast.err(e); load() } finally { setSaving(false) }
  }
  const setSegments = (next: Seg[]) => { if (reel) { setReel({ ...reel, segments: next, total_seconds: total(next), status: 'draft' }); save({ segments: next }) } }
  const setOpt = <K extends keyof Options>(k: K, v: Partial<Options[K]>) => {     // saved shortly after the last change
    if (!reel) return
    const options = { ...reel.options, [k]: { ...reel.options[k], ...v } }
    setReel({ ...reel, options, status: 'draft' })
    if (optTimer.current) window.clearTimeout(optTimer.current)
    optTimer.current = window.setTimeout(() => save({ options: { [k]: options[k] } }), 600)
  }

  // ---- waiting for a render job
  useEffect(() => {
    if (!job) return
    const t = window.setInterval(async () => {
      try {
        const j = await api.get<Job>(`/api/jobs/${job.id}`)
        setProgress(Math.round((j.progress || 0) * 100))
        if (j.status === 'completed') { window.clearInterval(t); setJob(null); toast.ok(`${job.what} is ready`); await load(); setView(job.what === 'Preview' ? 'preview' : 'final') }
        else if (j.status === 'failed' || j.status === 'cancelled') { window.clearInterval(t); setJob(null); toast.err(j.error || `${job.what} ${j.status}`); load() }
      } catch (e) { window.clearInterval(t); setJob(null); toast.err(e) }
    }, 2000)
    return () => window.clearInterval(t)
  }, [job, load, toast])

  // ---- drag across words
  useEffect(() => {
    if (!drag) return
    const up = () => {
      if (drag.a !== drag.b) setSegments(addRange(segs, rangeFromWords(words, drag.a, drag.b)))
      setDrag(null)
    }
    window.addEventListener('mouseup', up)
    return () => window.removeEventListener('mouseup', up)
  })
  useEffect(() => { if (hits.size) document.getElementById(`sent-${[...hits][0]}`)?.scrollIntoView({ block: 'center' }) }, [phrase]) // eslint-disable-line

  // ---- the preview that jumps over removed parts (no render needed)
  useEffect(() => {
    if (playIdx === null || view !== 'source') return
    let raf = 0
    const v = video.current
    if (!v) return
    const tick = () => {
      const r = previewTick(segs, playIdx, v.currentTime)
      if (r.done) { v.pause(); setPlayIdx(null); return }
      if (r.seek !== null) v.currentTime = r.seek
      if (r.idx !== playIdx) setPlayIdx(r.idx)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playIdx, segs, view])
  const playFrom = (i: number) => {
    if (!segs[i]) return
    setView('source')
    window.setTimeout(() => { const el = video.current; if (el) { el.currentTime = segs[i].start; el.play(); setPlayIdx(i) } }, 0)
  }

  if (err && !reel) return <ErrorBox error={err} retry={load} />
  if (!reel) return <Loading rows={3} />
  const busy = !!job
  const o = reel.options
  const over = reel.total_seconds > WARN_SECONDS
  const stepIdx = STEPS.findIndex(([k]) => k === reel.status)

  const run = async (what: string, path: string) => {
    try { const r = await api.post<{ job_id: number }>(path); setProgress(0); setJob({ id: r.job_id, what }); load() } catch (e) { toast.err(e) }
  }
  const textOf = (s: Seg) => words.filter((w) => inReel(w, [s])).map((w) => reel.caption_edits[w.id] ?? w.w).join(' ')
  const splitAtPlayhead = (i: number) => {
    const t = video.current?.currentTime ?? 0
    const next = split(segs, i, t)
    next === segs ? toast.info('Move the video to the point inside this segment where it should split, then press Split') : setSegments(next)
  }
  const saveEdit = async () => { if (editing) { await save({ caption_edits: { [editing.id]: editing.text } }); setEditing(null) } }
  const copy = async (text: string) => { try { await navigator.clipboard.writeText(text); toast.ok('Copied') } catch { toast.info('Select the text and copy it') } }
  const videoSrc = view === 'preview' ? `/api/editor/reels/${reel.id}/file/preview?t=${encodeURIComponent(reel.updated_at)}`
    : view === 'final' ? `/api/editor/reels/${reel.id}/file/final?t=${encodeURIComponent(reel.updated_at)}` : fileUrl.video(proj.video!.id)

  return (
    <div>
      <PageHeader title="Reel Editor" subtitle={proj.name} actions={<>
        <button className="btn-ghost" onClick={back}><ArrowLeft size={16} />All reels</button>
        <button className="btn-ghost" onClick={async () => { try { const d = await api.post<Reel>(`/api/editor/reels/${reel.id}/duplicate`); toast.ok('Duplicated'); open(d.id) } catch (e) { toast.err(e) } }}><Copy size={16} />Duplicate</button>
        <button className="btn-ghost" onClick={async () => { const note = window.prompt('Name this version (optional)') ; if (note === null) return; try { const v = await api.post<{ version: number }>(`/api/editor/reels/${reel.id}/versions`, { note }); toast.ok(`Saved as version ${v.version}`); load() } catch (e) { toast.err(e) } }}><Save size={16} />Save version</button>
        <button className="btn-ghost" onClick={async () => { if (await confirm({ title: 'Delete this reel?', body: 'The reel and its versions are removed. The sermon is not touched.', confirm: 'Delete', danger: true })) { try { await api.del(`/api/editor/reels/${reel.id}`); gone() } catch (e) { toast.err(e) } } }}><Trash2 size={16} />Delete</button></>} />

      <div className="card p-3 mb-4 flex flex-wrap items-center gap-3">
        <input className="input !w-72" value={reel.title} onChange={(e) => setReel({ ...reel, title: e.target.value })} onBlur={() => save({ title: reel.title })} aria-label="Reel title" />
        <div className="flex items-center gap-1 text-xs">{STEPS.map(([k, l], i) => <span key={k} className={`px-2 py-1 rounded-full border ${i === stepIdx ? 'border-accent text-ink bg-accent/15' : i < stepIdx ? 'border-ok/50 text-ok' : 'border-line text-muted'}`}>{l}</span>)}</div>
        <span className="text-xs text-muted flex items-center gap-1">{saving ? <><Spinner className="!w-3 !h-3" />Saving…</> : <><Check size={12} />Saved</>}</span>
        <div className="flex-1" />
        <button className="btn-ghost" disabled={busy || !segs.length} onClick={() => run('Preview', `/api/editor/reels/${reel.id}/preview`)}>{busy && job?.what === 'Preview' ? <Spinner /> : <Play size={16} />}Render preview</button>
        <button className="btn-primary" disabled={busy || reel.status !== 'ready'} title={reel.status === 'ready' ? '' : 'Render a preview and review it first'} onClick={() => run('Final render', `/api/editor/reels/${reel.id}/approve`)}>{busy && job?.what === 'Final render' ? <Spinner /> : <Check size={16} />}Approve &amp; render</button>
      </div>
      {busy && <div className="mb-3"><ProgressBar value={progress} /><p className="text-xs text-muted mt-1">{job?.what} rendering in the background… {progress}%</p></div>}

      <div className="grid xl:grid-cols-2 gap-5">
        {/* ---------------- transcript ---------------- */}
        <div className="card p-3 flex flex-col" style={{ height: '78vh' }}>
          <div className="flex items-center gap-2 mb-2">
            <div className="relative flex-1"><Search size={14} className="absolute left-3 top-3 text-muted" /><input className="input !pl-8" placeholder="Find a phrase in the sermon…" value={phrase} onChange={(e) => setPhrase(e.target.value)} /></div>
            {phrase && <span className="text-xs text-muted whitespace-nowrap">{hits.size} found</span>}
          </div>
          <p className="text-[11px] text-muted mb-2">Click <b>+</b> to add a sentence. Drag across words for your own range. Double-click a word to correct its caption text{tr.word_times !== 'spoken' ? ' · some word times are estimated (older transcript)' : ''}.</p>
          <div className="flex-1 overflow-y-auto pr-2 select-none text-sm leading-7">
            {tr.sentences.map((s) => (
              <div key={s.id} id={`sent-${s.id}`} className={`flex gap-2 items-start py-0.5 rounded ${hits.has(s.id) ? 'bg-warn/10' : ''}`}>
                <button className="shrink-0 mt-1 text-[11px] text-muted hover:text-ink w-14 text-right" title="Add this sentence to the reel" onClick={() => setSegments(addRange(segs, { start: s.start, end: s.end }))}>{fmt(s.start)} <Plus size={11} className="inline" /></button>
                <p className="flex-1">{s.words.map((w) => {
                  const i = words.indexOf(w)
                  const picked = !!drag && i >= Math.min(drag.a, drag.b) && i <= Math.max(drag.a, drag.b)
                  return editing?.id === w.id
                    ? <input key={w.id} autoFocus className="input !w-28 !py-0 !px-1 inline" value={editing.text} onChange={(e) => setEditing({ id: w.id, text: e.target.value })} onBlur={saveEdit}
                        onKeyDown={(e) => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') setEditing(null) }} />
                    : <WordSpan key={w.id} w={w} i={i} picked={picked} on={inReel(w, segs)} hit={false} edited={reel.caption_edits[w.id]}
                        onDown={(k) => setDrag({ a: k, b: k })} onEnter={(k) => setDrag((d) => (d ? { ...d, b: k } : d))} onEdit={(x) => setEditing({ id: x.id, text: reel.caption_edits[x.id] ?? x.w })} />
                })}</p>
              </div>))}
          </div>
        </div>

        {/* ---------------- timeline + preview ---------------- */}
        <div className="space-y-4">
          <div className="card overflow-hidden">
            <div className="flex gap-1 p-2 border-b border-line/60 text-xs">
              {([['source', 'Quick preview (jumps over cut parts)'], ['preview', 'Rendered preview'], ['final', 'Final']] as const).map(([k, l]) => (
                <button key={k} disabled={(k === 'preview' && !reel.has_preview) || (k === 'final' && !reel.asset_id)} onClick={() => { setPlayIdx(null); setView(k) }}
                  className={`px-2.5 py-1 rounded-lg disabled:opacity-40 ${view === k ? 'bg-accent/20 text-ink' : 'text-muted hover:text-ink'}`}>{l}</button>))}
            </div>
            <video ref={video} key={view + reel.updated_at} controls preload="metadata" className="w-full max-h-[34vh] bg-black" src={videoSrc} />
          </div>

          <div className="card p-3">
            <div className="flex items-center gap-3 mb-2">
              <h2 className="font-medium">Reel timeline</h2>
              <span className={`text-sm ${over ? 'text-warn' : 'text-muted'}`}>Total {fmt(reel.total_seconds)} ({reel.total_seconds.toFixed(1)} s)</span>
              <div className="flex-1" /><button className="btn-ghost !py-1" disabled={!segs.length} onClick={() => playFrom(0)}><Play size={14} />Play reel</button>
            </div>
            {over && <p className="text-xs text-warn mb-2">{reel.warnings[0] || `Over ${WARN_SECONDS} seconds`} Most platforms prefer 60 s or less - you can still keep it.</p>}
            {!segs.length && <p className="text-sm text-muted py-4 text-center">Nothing in this reel yet. Add sentences from the transcript on the left.</p>}
            {segs.map((s, i) => (
              <div key={i} draggable onDragStart={() => setDragSeg(i)} onDragOver={(e) => e.preventDefault()} onDrop={() => { if (dragSeg !== null) setSegments(reorder(segs, dragSeg, i)); setDragSeg(null) }}
                className={`rounded-xl border p-2 mb-2 ${playIdx === i ? 'border-accent bg-accent/10' : 'border-line/70'}`}>
                <div className="flex items-center gap-2 text-xs"><span className="cursor-grab text-muted" title="Drag to reorder">⋮⋮</span><b>{i + 1}</b>
                  <span>{fmt(s.start)} - {fmt(s.end)}</span><span className="text-muted">({(s.end - s.start).toFixed(1)} s)</span><div className="flex-1" />
                  <button className="btn-ghost !py-0.5 !px-2" onClick={() => playFrom(i)} title="Play from here"><Play size={12} /></button>
                  <button className="btn-ghost !py-0.5 !px-2" onClick={() => splitAtPlayhead(i)} title="Split at the video's current time"><Scissors size={12} /></button>
                  <button className="btn-ghost !py-0.5 !px-2" onClick={() => setSegments(remove(segs, i))} title="Remove this segment"><Trash2 size={12} /></button></div>
                <p className="text-xs text-muted line-clamp-2 mt-1">{textOf(s)}</p>
                <div className="flex gap-3 mt-1 text-[11px]">
                  <span>Start <button className="btn-ghost !py-0 !px-1.5" onClick={() => setSegments(trimByWord(segs, i, 'start', -1, words))}>◀ word</button><button className="btn-ghost !py-0 !px-1.5" onClick={() => setSegments(trimByWord(segs, i, 'start', 1, words))}>word ▶</button></span>
                  <span>End <button className="btn-ghost !py-0 !px-1.5" onClick={() => setSegments(trimByWord(segs, i, 'end', -1, words))}>◀ word</button><button className="btn-ghost !py-0 !px-1.5" onClick={() => setSegments(trimByWord(segs, i, 'end', 1, words))}>word ▶</button></span>
                </div>
              </div>))}
          </div>
        </div>
      </div>

      {/* ---------------- what to add ---------------- */}
      <div className="card p-4 mt-5">
        <Tabs value={tab} onChange={(t) => { setTab(t); if (t === 'cards' && !hooks.length) api.get<{ suggestions: string[] }>(`/api/editor/reels/${reel.id}/hook-suggestions`).then((r) => setHooks(r.suggestions)).catch(() => undefined) }}
          tabs={[{ id: 'captions', label: 'Captions' }, { id: 'cards', label: 'Hook, title & end card' }, { id: 'format', label: 'Format & audio' }, { id: 'thumb', label: 'Thumbnail' }, { id: 'posts', label: 'Post text' }, { id: 'dub', label: 'Dub / translate' }]} />
        <div className="mt-4 grid md:grid-cols-2 gap-4">
          {tab === 'captions' && <>
            <label className="flex items-center gap-2 text-sm md:col-span-2"><input type="checkbox" checked={o.caption.enabled} onChange={(e) => setOpt('caption', { enabled: e.target.checked })} />Burn captions into the video</label>
            <Field label="Style" hint="Changing the style never re-cuts the video."><select className="input" value={o.caption.template} onChange={(e) => setOpt('caption', { template: e.target.value })}>{CAPTION_STYLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
            <Field label="Font" hint="Leave empty for the default caption font"><input className="input" value={o.caption.font} onChange={(e) => setOpt('caption', { font: e.target.value })} placeholder="e.g. Segoe UI Black" /></Field>
            <Field label="Colour"><input type="color" className="h-10 w-20 rounded" value={o.caption.color} onChange={(e) => setOpt('caption', { color: e.target.value })} /></Field>
            <Field label={`Size ${o.caption.size}`}><input type="range" min={30} max={140} value={o.caption.size} onChange={(e) => setOpt('caption', { size: Number(e.target.value) })} className="w-full" /></Field>
            <p className="text-xs text-muted md:col-span-2">To fix a wrong word, double-click it in the transcript. Corrections change the text only, never the timing, and are kept with the reel ({Object.keys(reel.caption_edits).length} so far).</p>
          </>}
          {tab === 'cards' && <>
            <div className="space-y-2"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={o.hook.enabled} onChange={(e) => setOpt('hook', { enabled: e.target.checked })} />Hook card at the start</label>
              <input className="input" placeholder="Hook text" value={o.hook.text} onChange={(e) => setOpt('hook', { text: e.target.value })} />
              {hooks.length > 0 && <div className="flex flex-wrap gap-1.5">{hooks.map((h) => <button key={h} className="chip hover:bg-accent/20" onClick={() => setOpt('hook', { enabled: true, text: h })}><Sparkles size={11} />{h}</button>)}</div>}</div>
            <div className="space-y-2"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={o.lower_third.enabled} onChange={(e) => setOpt('lower_third', { enabled: e.target.checked })} />Title / lower third</label>
              <input className="input" placeholder="Speaker name" value={o.lower_third.name} onChange={(e) => setOpt('lower_third', { name: e.target.value })} />
              <input className="input" placeholder="Role or topic (optional)" value={o.lower_third.role} onChange={(e) => setOpt('lower_third', { role: e.target.value })} /></div>
            <div className="space-y-2"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={o.end_card.enabled} onChange={(e) => setOpt('end_card', { enabled: e.target.checked })} />End card</label>
              <input className="input" placeholder="e.g. Follow for more" value={o.end_card.text} onChange={(e) => setOpt('end_card', { text: e.target.value })} /></div>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={o.watermark.enabled} onChange={(e) => setOpt('watermark', { enabled: e.target.checked })} />Logo / watermark (from Brand &amp; Templates)</label>
          </>}
          {tab === 'format' && <>
            <Field label="Aspect ratio" hint="Changing it re-cuts the video."><div className="flex gap-2">{['9:16', '1:1', '16:9'].map((a) => <button key={a} className={`btn-ghost ${reel.aspect === a ? '!border-accent !text-ink' : ''}`} onClick={() => save({ aspect: a })}>{a}</button>)}</div></Field>
            <div />
            <Field label="Background music (optional)" hint="Off by default. Type the full path of an audio file on this computer."><input className="input" value={o.music.path} onChange={(e) => setOpt('music', { path: e.target.value })} placeholder="C:\Music\track.mp3 (empty = no music)" /></Field>
            <Field label={`Music volume ${Math.round(o.music.volume * 100)}%`}><input type="range" min={0} max={100} value={Math.round(o.music.volume * 100)} onChange={(e) => setOpt('music', { volume: Number(e.target.value) / 100 })} className="w-full" disabled={!o.music.path} /></Field>
          </>}
          {tab === 'thumb' && <>
            <div className="space-y-2">
              <Field label={`Frame at ${(o.thumbnail.time ?? 0).toFixed(1)} s of the reel`}><input type="range" min={0} max={Math.max(1, reel.total_seconds)} step={0.1} value={o.thumbnail.time ?? 0} onChange={(e) => setOpt('thumbnail', { time: Number(e.target.value) })} className="w-full" /></Field>
              <Field label="Text on the thumbnail (optional)"><input className="input" value={o.thumbnail.text} onChange={(e) => setOpt('thumbnail', { text: e.target.value })} /></Field>
              <button className="btn-ghost" disabled={!reel.has_preview} title={reel.has_preview ? '' : 'Render a preview first'} onClick={async () => { try { await api.post(`/api/editor/reels/${reel.id}/thumbnail`, { time: o.thumbnail.time ?? 0, text: o.thumbnail.text }); toast.ok('Thumbnail made'); load() } catch (e) { toast.err(e) } }}>Make thumbnail</button></div>
            {o.thumbnail.path ? <img alt="Thumbnail" className="rounded-xl border border-line/70 max-h-60" src={`/api/editor/reels/${reel.id}/file/thumbnail?t=${encodeURIComponent(reel.updated_at)}`} /> : <p className="text-sm text-muted">No thumbnail yet.</p>}
          </>}
          {tab === 'posts' && <>
            <div className="md:col-span-2"><button className="btn-ghost" onClick={async () => { try { const d = await api.post<Record<string, string>>(`/api/editor/reels/${reel.id}/posts/draft`); setOpt('posts', d) } catch (e) { toast.err(e) } }}><Sparkles size={16} />Draft from this reel's words</button></div>
            {Object.keys(PLATFORM_LABEL).map((p) => (
              <Field key={p} label={PLATFORM_LABEL[p]}><textarea className="input" rows={5} value={o.posts[p] ?? ''} onChange={(e) => setOpt('posts', { [p]: e.target.value })} />
                <button className="btn-ghost !py-1 mt-1" onClick={() => copy(o.posts[p] ?? '')}><Copy size={13} />Copy</button></Field>))}
          </>}
          {tab === 'dub' && <>
            <p className="text-sm text-muted md:col-span-2">Dubbing and other-language versions are only available once the reel is <b>Approved</b> and its final render is done. They use the edited cut of the reel.</p>
            <Field label="Language"><select className="input" value={target} onChange={(e) => setTarget(e.target.value)}>{LANGUAGES.map((l) => <option key={l}>{l}</option>)}</select></Field>
            <label className="flex items-center gap-2 text-sm self-end"><input type="checkbox" checked={dub} disabled={!DUB_LANGUAGES.has(target)} onChange={(e) => setDub(e.target.checked)} />Dub the voice (not just subtitles)</label>
            <div><button className="btn-primary" disabled={!(reel.status === 'approved' || reel.status === 'rendered') || !reel.asset_id}
              onClick={async () => { try { const r = await api.post<{ job_id: number }>(`/api/editor/reels/${reel.id}/translate`, { target, dub }); toast.ok(`Queued (job ${r.job_id}) - see the Processing Centre`) } catch (e) { toast.err(e) } }}><Languages size={16} />Start</button></div>
          </>}
        </div>
      </div>
    </div>
  )
}
