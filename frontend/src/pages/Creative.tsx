import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, Cpu, Download, History, Pencil, RefreshCw, Sparkles, ThumbsDown, ThumbsUp, Trash2, Wand2 } from 'lucide-react'
import { api, Asset, fileUrl, Job } from '../api'
import { Empty, ErrorBox, Field, Loading, Modal, PageHeader, ProgressBar, Spinner, StatusBadge, useApi, useConfirm, useToast } from '../ui'
import AssetDetail from '../components/AssetDetail'

type Status = {
  available: boolean; reason: string; backend?: string; model?: string
  hardware: { gpu: { name: string; total_gb: number; free_gb: number } | null; ram: { total_gb: number; free_gb: number }; recommendation: { model: string | null; reason: string } }
  styles: { id: string; name: string; summary: string; visual_source: string }[]
  formats: { id: string; label: string; size: number[]; max_words: number; note: string }[]
  default_styles: string[]; benchmark: Record<string, any>
}
type Brief = {
  message: string; emotional_theme: string; audience: string; headline: string; supporting_text: string; scripture_reference: string
  creative_direction: string; source?: string; candidates: { headlines: string[]; metaphors: { subject: string; environment: string }[]; key_points: string[] }
  [k: string]: any
}
type Workspace = { project: { id: number; name: string }; source: { title: string; main_theme: string; summary: string; key_points: string[]; visual_theme: string; encouragement?: string[] } | null; brief: Brief | null; assets: Asset[] }
const THEMES = ['warfare', 'spirit', 'grace', 'peace', 'hope', 'faith', 'love', 'strength', 'growth']

export default function Creative() {
  const toast = useToast()
  const [sp, setSp] = useSearchParams()
  const [pid, setPid] = useState(sp.get('project') || '')
  const [styles, setStyles] = useState<string[] | null>(null)
  const [fmts, setFmts] = useState<string[]>(['poster'])
  const [busy, setBusy] = useState('')
  const [detail, setDetail] = useState<number | null>(null)
  const [preview, setPreview] = useState<Asset | null>(null)
  const [draft, setDraft] = useState<Brief | null>(null)
  const projects = useApi(() => api.get<{ items: { id: number; name: string }[] }>('/api/projects?limit=200'), [])
  const status = useApi(() => api.get<Status>('/api/creative/status'), [], 15000)
  const ws = useApi(() => (pid ? api.get<Workspace>(`/api/projects/${pid}/creative`) : Promise.resolve(null)), [pid], 4000)
  const jobs = useApi(() => (pid ? api.get<{ items: Job[] }>(`/api/jobs?project_id=${pid}&limit=6`) : Promise.resolve({ items: [] as Job[] })), [pid], 2500)
  const running = (jobs.data?.items ?? []).find((j) => j.job_type.startsWith('creative') && ['queued', 'running'].includes(j.status))
  const lastFail = (jobs.data?.items ?? []).find((j) => j.job_type.startsWith('creative') && j.status === 'failed')
  useEffect(() => { if (status.data && styles === null) setStyles(status.data.default_styles) }, [status.data, styles])
  useEffect(() => { setDraft(ws.data?.brief ? structuredClone(ws.data.brief) : null) }, [ws.data?.brief])
  useEffect(() => { if (!running) ws.reload() }, [running?.id]) // eslint-disable-line
  const st = status.data
  const run = async (key: string, fn: () => Promise<unknown>, ok: string) => { setBusy(key); try { await fn(); toast.ok(ok); ws.reload(); jobs.reload() } catch (e) { toast.err(e) } finally { setBusy('') } }
  const photoSelected = (styles ?? []).some((s) => st?.styles.find((x) => x.id === s)?.visual_source === 'photo')
  const blocked = photoSelected && st && !st.available
  const toggle = (arr: string[], v: string, set: (a: string[]) => void) => set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v])

  // concepts grouped by format, newest first
  const groups = useMemo(() => {
    const g: Record<string, Asset[]> = {}
    for (const a of ws.data?.assets ?? []) (g[a.meta.format] ||= []).push(a)
    return g
  }, [ws.data?.assets])

  return (
    <>
      <PageHeader title="Creative Studio" subtitle="Turn a sermon into distinct, professionally designed visuals. A picture is generated first (never with text), then the typography is designed on top and every result is quality-checked." />

      {st && <div className={`card p-4 mb-5 flex flex-wrap gap-4 items-start ${st.available ? '' : 'border-amber-500/40'}`}>
        <Cpu size={20} className={st.available ? 'text-teal' : 'text-amber-400'} />
        <div className="flex-1 min-w-[16rem] text-sm">
          {st.available ? <p><b>Image model ready:</b> {st.model} <span className="text-muted">({st.backend})</span></p>
            : <p><b className="text-amber-400">No image-generation model is available.</b> <span className="text-muted">{st.reason}</span></p>}
          <p className="text-xs text-muted mt-1">GPU: {st.hardware.gpu ? `${st.hardware.gpu.name}, ${st.hardware.gpu.total_gb} GB (${st.hardware.gpu.free_gb} GB free)` : 'none detected'} · RAM: {st.hardware.ram.free_gb} of {st.hardware.ram.total_gb} GB free · {st.hardware.recommendation.reason}</p>
          {st.benchmark?.seconds_first_image !== undefined && <p className="text-xs text-muted mt-1">Last benchmark: {st.benchmark.seconds_first_image}s for a {st.benchmark.size?.join('×')} picture{st.benchmark.peak_vram_gb ? `, peak ${st.benchmark.peak_vram_gb} GB VRAM` : ''} ({st.benchmark.model}).</p>}
          {!st.available && <p className="text-xs text-muted mt-1">Without a model only the <b>Minimalist typography</b> engine works (it is labelled as typographic, never as AI artwork). Backends and models are set in Settings → Creative Studio.</p>}
        </div>
        <button className="btn-ghost" disabled={!st.available || busy === 'bench'} onClick={() => run('bench', () => api.post('/api/creative/benchmark'), 'Benchmark queued - see Processing Centre')}>Run benchmark</button>
      </div>}

      <div className="card p-4 mb-5 flex flex-wrap gap-3 items-end">
        <Field label="Project"><select className="input min-w-[16rem]" value={pid} onChange={(e) => { setPid(e.target.value); setSp(e.target.value ? { project: e.target.value } : {}) }}>
          <option value="">Choose a project…</option>{(projects.data?.items ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
      </div>

      {!pid ? <Empty icon={<Wand2 size={36} />} title="Choose a project" hint="Creatives are designed from the project's transcript, so it must have been transcribed." /> : ws.loading && !ws.data ? <Loading rows={3} /> : ws.error && !ws.data ? <ErrorBox error={ws.error} retry={ws.reload} /> : !ws.data?.source ? (
        <Empty icon={<Wand2 size={36} />} title="No transcript yet" hint="Run the pipeline on this project first, then come back." />
      ) : (<>
        <div className="grid lg:grid-cols-2 gap-5 mb-5">
          <div className="card p-4">
            <h2 className="font-semibold mb-2">Content source</h2>
            <p className="text-sm"><b>{ws.data.source.title}</b></p>
            <p className="text-xs text-muted mb-2">Theme: {ws.data.source.main_theme}</p>
            <p className="text-sm text-muted">{ws.data.source.summary}</p>
            {ws.data.source.key_points.length > 0 && <ul className="mt-2 text-sm list-disc pl-5 text-muted">{ws.data.source.key_points.map((k, i) => <li key={i}>{k}</li>)}</ul>}
          </div>
          <div className="card p-4">
            <div className="flex items-center justify-between mb-2"><h2 className="font-semibold">Creative brief</h2>
              <button className="btn-ghost" disabled={busy === 'brief' || !!running} onClick={() => run('brief', () => api.post(`/api/projects/${pid}/creative/brief`), 'Writing the brief from the transcript…')}><Sparkles size={14} />{draft ? 'Rewrite from transcript' : 'Write from transcript'}</button></div>
            {!draft ? <p className="text-sm text-muted">No brief yet. It is written from the actual transcript: the message, emotional theme, audience, three different visual metaphors and three different headlines. It is also created automatically when you generate.</p> : (
              <div className="space-y-2 text-sm">
                <Field label="Message"><textarea className="input" rows={2} value={draft.message} onChange={(e) => setDraft({ ...draft, message: e.target.value })} /></Field>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="Emotional theme"><select className="input" value={draft.emotional_theme} onChange={(e) => setDraft({ ...draft, emotional_theme: e.target.value })}>{THEMES.map((t) => <option key={t}>{t}</option>)}</select></Field>
                  <Field label="Audience"><input className="input" value={draft.audience} onChange={(e) => setDraft({ ...draft, audience: e.target.value })} /></Field>
                </div>
                <Field label="Headline options (one per concept)">{draft.candidates.headlines.map((h, i) => <input key={i} className="input mb-1" value={h} onChange={(e) => { const c = [...draft.candidates.headlines]; c[i] = e.target.value; setDraft({ ...draft, headline: i === 0 ? e.target.value : draft.headline, candidates: { ...draft.candidates, headlines: c } }) }} />)}</Field>
                <Field label="Visual metaphors (one per concept)">{draft.candidates.metaphors.map((m, i) => <div key={i} className="grid grid-cols-2 gap-1 mb-1"><input className="input" value={m.subject} onChange={(e) => { const c = [...draft.candidates.metaphors]; c[i] = { ...m, subject: e.target.value }; setDraft({ ...draft, candidates: { ...draft.candidates, metaphors: c } }) }} /><input className="input" value={m.environment} onChange={(e) => { const c = [...draft.candidates.metaphors]; c[i] = { ...m, environment: e.target.value }; setDraft({ ...draft, candidates: { ...draft.candidates, metaphors: c } }) }} /></div>)}</Field>
                <Field label="Supporting line"><input className="input" value={draft.supporting_text} onChange={(e) => setDraft({ ...draft, supporting_text: e.target.value })} /></Field>
                <p className="text-xs text-muted">Scripture: <b>{draft.scripture_reference || 'none'}</b> - only ever a reference the speaker actually said. Brief source: {draft.source}.</p>
                <button className="btn-primary" disabled={busy === 'save'} onClick={() => run('save', () => api.put(`/api/projects/${pid}/creative/brief`, { brief: draft }), 'Brief saved')}>Save brief</button>
              </div>)}
          </div>
        </div>

        <div className="card p-4 mb-5">
          <h2 className="font-semibold mb-3">Design engines</h2>
          <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-2 mb-4">{st?.styles.map((s) => { const on = (styles ?? []).includes(s.id); return (
            <button key={s.id} onClick={() => toggle(styles ?? [], s.id, setStyles)} className={`text-left rounded-xl border p-3 transition ${on ? 'border-primary/70 bg-primary/10' : 'border-line hover:border-line/100'}`}>
              <div className="flex items-center justify-between"><b className="text-sm">{s.name}</b>{on && <CheckCircle2 size={15} className="text-accent" />}</div>
              <p className="text-xs text-muted mt-1">{s.summary}</p>{s.visual_source === 'typographic' && <span className="chip mt-2 text-[10px]">No AI picture</span>}
            </button>) })}</div>
          <h2 className="font-semibold mb-2">Formats</h2>
          <div className="flex flex-wrap gap-2 mb-4">{st?.formats.filter((f) => f.id !== 'hook_story').map((f) => (
            <button key={f.id} title={f.note} onClick={() => toggle(fmts, f.id, setFmts)} className={`chip !px-3 !py-1.5 ${fmts.includes(f.id) ? 'bg-primary/25 border-primary/60' : 'border-line text-muted hover:text-ink'}`}>{f.label} <span className="opacity-60">{f.size.join('×')}</span></button>))}
            <button onClick={() => toggle(fmts, 'hook_story', setFmts)} className={`chip !px-3 !py-1.5 ${fmts.includes('hook_story') ? 'bg-primary/25 border-primary/60' : 'border-line text-muted hover:text-ink'}`}>Hook image 9:16 <span className="opacity-60">1080×1920</span></button></div>
          <p className="text-xs text-muted mb-3">Each format is composed for its own size, safe areas and text zones; it is not a resized copy. Selecting {(styles ?? []).length} engine(s) × {fmts.length} format(s) makes {(styles ?? []).length * fmts.length} concept(s); models run one after another and are unloaded afterwards.</p>
          {blocked && <p className="text-sm text-amber-400 mb-3"><AlertTriangle size={14} className="inline mr-1" />The selected engines need an image model, which is not available. Choose only “Minimalist typography”, or set one up (see the banner above).</p>}
          {lastFail && !running && <p className="text-sm text-danger mb-3">Last attempt failed: {lastFail.error}</p>}
          <button className="btn-primary" disabled={!!running || !(styles ?? []).length || !fmts.length || !!blocked || busy === 'gen'}
            onClick={() => run('gen', () => api.post(`/api/projects/${pid}/creative/generate`, { formats: fmts, styles }), 'Generating concepts…')}><Wand2 size={15} />Generate {(styles ?? []).length * fmts.length} concept{(styles ?? []).length * fmts.length === 1 ? '' : 's'}</button>
          {(ws.data.source.encouragement?.length ?? 0) > 0 && <button className="btn-ghost ml-2" disabled={!!running || !st?.available}
            title="Uses the sermon's own encouragement hooks, one per post, with the Emotional and Modern engines"
            onClick={() => run('enc', () => api.post(`/api/projects/${pid}/creative/generate`, { formats: ['feed_post'], styles: ['emotional', 'modern'], headlines: ws.data!.source!.encouragement!.slice(0, 2) }), 'Designing the encouragement posts…')}>Encouragement posts ({Math.min(2, ws.data.source.encouragement!.length)})</button>}
          {running && <div className="mt-3"><p className="text-sm text-muted flex items-center gap-2"><Spinner />{running.step || 'Queued'}</p><ProgressBar value={running.progress} className="mt-1" /></div>}
        </div>

        {Object.keys(groups).length === 0 ? <Empty icon={<Sparkles size={36} />} title="No concepts yet" hint="Choose engines and formats above and press Generate. Nothing is ever approved automatically." /> :
          Object.entries(groups).map(([f, items]) => (
            <div key={f} className="mb-6">
              <h2 className="font-semibold mb-2">{items[0].meta.format_label} <span className="text-xs text-muted">({items.length})</span></h2>
              <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4">{items.map((a) => <ConceptCard key={a.id} a={a} onPreview={() => setPreview(a)} onHistory={() => setDetail(a.id)} onChanged={() => { ws.reload(); jobs.reload() }} />)}</div>
            </div>))}
      </>)}

      {preview && <PreviewModal a={preview} onClose={() => setPreview(null)} />}
      {detail && <AssetDetail id={detail} onClose={() => setDetail(null)} onChanged={() => ws.reload()} />}
    </>
  )
}

function ConceptCard({ a, onPreview, onHistory, onChanged }: { a: Asset; onPreview: () => void; onHistory: () => void; onChanged: () => void }) {
  const toast = useToast(), confirm = useConfirm()
  const [edit, setEdit] = useState(false)
  const [head, setHead] = useState(a.title)
  const [busy, setBusy] = useState('')
  const qc = a.meta.qc || { passed: false, failed: [], checks: [] }
  const act = async (key: string, fn: () => Promise<unknown>, ok: string) => { setBusy(key); try { await fn(); toast.ok(ok); onChanged() } catch (e) { toast.err(e) } finally { setBusy('') } }
  return (
    <div className="card overflow-hidden">
      <button onClick={onPreview} className="block w-full bg-black/30"><img src={fileUrl.preview(a.id, a.version)} alt={a.title} className="w-full max-h-[420px] object-contain" loading="lazy" /></button>
      <div className="p-3 space-y-2">
        <div className="flex items-center justify-between gap-2"><b className="text-sm">{a.meta.style_name}</b><StatusBadge status={a.status} /></div>
        {edit ? (
          <div className="flex gap-1"><input className="input" value={head} onChange={(e) => setHead(e.target.value)} />
            <button className="btn-primary" disabled={busy === 'edit'} onClick={() => act('edit', () => api.post(`/api/creative/${a.id}/recompose`, { headline: head }), 'Re-designing with your headline…').then(() => setEdit(false))}>Apply</button></div>
        ) : <p className="text-sm">{a.title}</p>}
        <p className={`text-xs flex items-start gap-1 ${qc.passed ? 'text-teal' : 'text-amber-400'}`}>
          {qc.passed ? <CheckCircle2 size={13} className="mt-0.5" /> : <AlertTriangle size={13} className="mt-0.5" />}
          {qc.passed ? `Quality checks passed (${Math.round(qc.score * 100)}%) - still needs your approval` : `Needs attention: ${qc.failed.join(', ').replace(/_/g, ' ')}`}</p>
        <p className="text-[11px] text-muted">{a.meta.visual_source === 'photo' ? `Picture by ${a.meta.model}. Check that it fits the message.` : 'Typographic design - no AI artwork.'}</p>
        <div className="flex flex-wrap gap-1.5 pt-1">
          <button className="btn-ghost !px-2" title="Edit headline" onClick={() => setEdit(!edit)}><Pencil size={14} /></button>
          <button className="btn-ghost !px-2" title="Regenerate picture" disabled={busy === 'regen'} onClick={() => act('regen', () => api.post(`/api/creative/${a.id}/regenerate`), 'Regenerating…')}><RefreshCw size={14} /></button>
          <a className="btn-ghost !px-2" title="Download" href={fileUrl.asset(a.id, true)}><Download size={14} /></a>
          <button className="btn-ghost !px-2" title="History" onClick={onHistory}><History size={14} /></button>
          <button className="btn-ghost !px-2 text-teal" title="Approve" disabled={busy === 'ok'} onClick={() => act('ok', () => api.post(`/api/assets/${a.id}/review`, { action: 'approve' }), 'Approved')}><ThumbsUp size={14} /></button>
          <button className="btn-ghost !px-2 text-danger" title="Reject" disabled={busy === 'no'} onClick={() => act('no', () => api.post(`/api/assets/${a.id}/review`, { action: 'reject', reason: 'Rejected in Creative Studio' }), 'Rejected')}><ThumbsDown size={14} /></button>
          <button className="btn-danger !px-2 ml-auto" title="Delete" aria-label="Delete this creative" disabled={busy === 'del'} onClick={async () => {
            if (await confirm({ title: 'Delete this creative?', body: 'The picture and all its versions are removed from disk. This cannot be undone.', confirm: 'Delete', danger: true })) act('del', () => api.del(`/api/assets/${a.id}`), 'Deleted')
          }}><Trash2 size={14} /></button>
        </div>
      </div>
    </div>
  )
}

function PreviewModal({ a, onClose }: { a: Asset; onClose: () => void }) {
  const qc = a.meta.qc || { checks: [] }
  const icon = (s: string) => (s === 'pass' ? '✓' : s === 'fail' ? '✗' : s === 'warn' ? '!' : '•')
  return (
    <Modal title={`${a.meta.style_name} · ${a.meta.format_label}`} onClose={onClose} width="max-w-6xl">
      <div className="grid lg:grid-cols-[minmax(0,1fr)_22rem] gap-4">
        <img src={fileUrl.asset(a.id)} alt={a.title} className="w-full max-h-[80vh] object-contain bg-black/40 rounded-lg" />
        <div className="text-sm space-y-3 overflow-auto max-h-[80vh]">
          <div><b>Headline</b><p className="text-muted">{a.title}</p></div>
          {a.meta.concept && <div><b>Creative brief for this concept</b><ul className="text-xs text-muted space-y-1 mt-1">
            {['visual_subject', 'environment', 'lighting', 'colour_palette', 'composition', 'creative_direction'].map((k) => a.meta.concept[k] ? <li key={k}><span className="text-ink">{k.replace(/_/g, ' ')}:</span> {a.meta.concept[k]}</li> : null)}</ul></div>}
          {a.meta.prompt && <div><b>Image prompt</b><p className="text-xs text-muted">{a.meta.prompt}</p></div>}
          <div><b>Quality checks</b><ul className="text-xs mt-1 space-y-1">{qc.checks.map((c: any) => <li key={c.name} className={c.status === 'fail' ? 'text-danger' : c.status === 'warn' || c.status === 'manual' ? 'text-amber-400' : 'text-muted'}><span className="font-mono">{icon(c.status)}</span> {c.name.replace(/_/g, ' ')} - {c.detail}</li>)}</ul></div>
        </div>
      </div>
    </Modal>
  )
}
