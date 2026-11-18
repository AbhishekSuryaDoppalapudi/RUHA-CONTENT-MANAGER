import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { CalendarPlus, Check, Download, History, MessageSquare, RefreshCw, RotateCcw, Save, Trash2, X } from 'lucide-react'
import { api, AssetDetail as Detail, fileUrl } from '../api'
import { Field, Loading, Modal, ErrorBox, StatusBadge, Spinner, fmtDate, fmtDur, useApi, useConfirm, useToast, label } from '../ui'
import { KIND_LABEL } from './AssetCard'

type Props = { id: number; onClose: () => void; onChanged?: () => void }

export default function AssetDetail({ id, onClose, onChanged }: Props) {
  const toast = useToast(), confirm = useConfirm()
  const { data: a, error, loading, reload, setData } = useApi(() => api.get<Detail>(`/api/assets/${id}`), [id])
  const [f, setF] = useState({ title: '', caption: '', description: '', hashtags: '' })
  const [sub, setSub] = useState<Record<string, string>>({})
  const [times, setTimes] = useState({ start: '', end: '' })
  const [busy, setBusy] = useState('')
  const [reason, setReason] = useState('')
  const [comment, setComment] = useState('')
  const [text, setText] = useState<string | null>(null)
  const [tab, setTab] = useState<'edit' | 'history'>('edit')

  useEffect(() => {
    if (!a) return
    setF({ title: a.title, caption: a.caption, description: a.description, hashtags: a.hashtags.join(' ') })
    setSub({ ...(a.poster || {}), ...(a.social_post || {}) } as any)
    setTimes({ start: String(a.reel?.start ?? ''), end: String(a.reel?.end ?? '') })
    if (['transcript', 'subtitle', 'translation_subtitle', 'caption'].includes(a.kind)) api.get<{ text: string }>(`/api/assets/${id}/text`).then((r) => setText(r.text)).catch(() => setText(null))
  }, [a?.version, a?.id]) // eslint-disable-line

  const run = async (name: string, fn: () => Promise<any>, ok: string) => {
    setBusy(name)
    try { const r = await fn(); toast.ok(ok); onChanged?.(); return r } catch (e) { toast.err(e) } finally { setBusy('') }
  }
  const save = () => run('save', async () => {
    const body: any = { title: f.title, caption: f.caption, description: f.description, hashtags: f.hashtags.split(/[\s,]+/).filter(Boolean) }
    if (a!.poster) body.poster = { headline: sub.headline, subheadline: sub.subheadline, quote: sub.quote }
    if (a!.social_post) body.social_post = { headline: sub.headline, body: sub.body, reference: sub.reference }
    setData(await api.patch<Detail>(`/api/assets/${id}`, body))
  }, 'Saved as a new version')
  const review = (action: string) => run(action, async () => {
    setData(await api.post<Detail>(`/api/assets/${id}/review`, { action, reason: action === 'comment' ? '' : reason, comment: action === 'comment' ? comment : '' }))
    setReason(''); setComment('')
  }, action === 'approve' ? 'Approved' : action === 'reject' ? 'Rejected' : action === 'revise' ? 'Revision requested' : 'Comment added')
  const regenerate = (body: any) => run('regen', () => api.post(`/api/assets/${id}/regenerate`, body), 'Queued - the new version appears when the job finishes (see Processing)')
  const del = async () => {
    if (!(await confirm({ title: 'Delete this asset?', body: 'The file and all its versions are removed from disk. This cannot be undone.', confirm: 'Delete', danger: true }))) return
    await run('del', () => api.del(`/api/assets/${id}`), 'Asset deleted'); onClose()
  }
  const toCalendar = () => run('cal', () => api.post('/api/calendar', { title: a!.title || KIND_LABEL[a!.kind], asset_id: a!.id, scheduled_for: new Date(Date.now() + 864e5).toISOString().slice(0, 16) + ':00' }), 'Added to the calendar as a draft')

  const isReel = a?.kind === 'main_reel' || a?.kind === 'short_reel' || a?.kind === 'image_reel'
  const isGraphic = a?.kind === 'poster' || a?.kind === 'social_post'
  const reviewable = a && ['main_reel', 'short_reel', 'image_reel', 'poster', 'social_post', 'creative'].includes(a.kind)

  return (
    <Modal title={a ? `${KIND_LABEL[a.kind] || label(a.kind)} · ${a.slot || ''}` : 'Asset'} onClose={onClose} width="max-w-5xl">
      {loading && !a ? <Loading rows={2} /> : error ? <ErrorBox error={error} retry={reload} /> : a && (
        <div className="grid md:grid-cols-[minmax(0,5fr)_minmax(0,6fr)] gap-5">
          {/* ---------- preview ---------- */}
          <div className="space-y-3">
            <div className="rounded-2xl overflow-hidden bg-black/50 flex items-center justify-center border border-line/70">
              {a.has_file && (isReel || a.kind === 'translation_video') ? (
                <video key={`${a.id}-${a.version}`} controls preload="metadata" poster={a.has_preview ? fileUrl.preview(a.id, a.version) : undefined}
                  src={`${fileUrl.asset(a.id)}${`?v=${a.version}`}`} className={`w-full ${isReel ? 'max-h-[68vh]' : 'max-h-[50vh]'}`} />
              ) : a.has_file && isGraphic ? (
                <img src={`${fileUrl.asset(a.id)}?v=${a.version}`} alt={a.title} className="w-full max-h-[68vh] object-contain" />
              ) : a.has_file && a.kind === 'audio' ? <audio controls src={fileUrl.asset(a.id)} className="w-full m-6" />
              : text !== null ? <pre className="text-xs p-4 w-full max-h-[60vh] overflow-auto whitespace-pre-wrap text-muted">{text}</pre>
              : <p className="p-10 text-sm text-muted">{a.has_file ? 'No preview for this file type.' : 'The file is missing on disk.'}</p>}
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
              <StatusBadge status={a.status} /> <span>v{a.version}</span>
              {a.reel && <span>{fmtDur(a.reel.start)} → {fmtDur(a.reel.end)} ({fmtDur(a.reel.end - a.reel.start)})</span>}
              <span>Updated {fmtDate(a.updated_at)}</span>
              <Link className="underline hover:text-ink" to={`/projects/${a.project_id}`} onClick={onClose}>{(a as any).project_name}</Link>
            </div>
            {a.meta?.similar_to?.length > 0 && <p className="text-xs text-warn bg-warn/10 border border-warn/30 rounded-xl p-2">Similar to earlier content in your memory - consider regenerating.</p>}
            <div className="flex flex-wrap gap-2">
              <a className="btn-ghost" href={fileUrl.asset(a.id, true)}><Download size={15} />Download</a>
              {reviewable && <button className="btn-ghost" onClick={toCalendar} disabled={busy === 'cal'}><CalendarPlus size={15} />Schedule</button>}
              <button className="btn-danger ml-auto" onClick={del}><Trash2 size={15} />Delete</button>
            </div>
          </div>

          {/* ---------- editor / review ---------- */}
          <div className="space-y-4 min-w-0">
            <div className="flex gap-1 text-sm">
              <button className={tab === 'edit' ? 'btn-primary' : 'btn-ghost'} onClick={() => setTab('edit')}>Details & review</button>
              <button className={tab === 'history' ? 'btn-primary' : 'btn-ghost'} onClick={() => setTab('history')}><History size={15} />History ({a.versions.length})</button>
            </div>

            {tab === 'edit' ? (<>
              <Field label="Title"><input className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
              {isGraphic && (a.poster ? (<>
                <Field label="Headline (on image)"><input className="input" value={sub.headline || ''} onChange={(e) => setSub({ ...sub, headline: e.target.value })} /></Field>
                <Field label="Supporting quote (on image)"><textarea className="input" rows={2} value={sub.quote || ''} onChange={(e) => setSub({ ...sub, quote: e.target.value })} /></Field>
              </>) : (<>
                <Field label="Headline (on image)"><input className="input" value={sub.headline || ''} onChange={(e) => setSub({ ...sub, headline: e.target.value })} /></Field>
                <Field label="Message (on image)"><textarea className="input" rows={3} value={sub.body || ''} onChange={(e) => setSub({ ...sub, body: e.target.value })} /></Field>
                <Field label="Reference" hint="Only keep a reference the speaker actually cited."><input className="input" value={sub.reference || ''} onChange={(e) => setSub({ ...sub, reference: e.target.value })} /></Field>
              </>))}
              <Field label="Caption"><textarea className="input" rows={3} value={f.caption} onChange={(e) => setF({ ...f, caption: e.target.value })} /></Field>
              <Field label="Description"><textarea className="input" rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
              <Field label="Hashtags" hint="Separate with spaces"><input className="input" value={f.hashtags} onChange={(e) => setF({ ...f, hashtags: e.target.value })} /></Field>
              <div className="flex flex-wrap gap-2">
                <button className="btn-primary" onClick={save} disabled={busy === 'save'}>{busy === 'save' ? <Spinner /> : <Save size={15} />}Save changes</button>
                {isGraphic && <button className="btn-ghost" disabled={busy === 'regen'} onClick={() => regenerate({ mode: a.kind === 'social_post' ? 'regenerate' : 'rerender' })}><RefreshCw size={15} />{a.kind === 'social_post' ? 'Regenerate with AI' : 'Redraw'}</button>}
                {isGraphic && <select className="input !w-auto" value="" onChange={(e) => e.target.value && regenerate({ mode: 'rerender', layout: e.target.value })} aria-label="Change layout">
                  <option value="">Change layout…</option><option value="classic">Classic</option><option value="band">Band</option><option value="centered">Centered</option></select>}
              </div>
              {isReel && (
                <div className="rounded-xl border border-line/70 p-3 space-y-2">
                  <p className="text-xs text-muted">Trim and re-render (creates a new version; the previous file is kept). Source video is never modified.</p>
                  <div className="grid grid-cols-2 gap-2">
                    <Field label="Start (s)"><input className="input" type="number" min={0} value={times.start} onChange={(e) => setTimes({ ...times, start: e.target.value })} /></Field>
                    <Field label="End (s)"><input className="input" type="number" min={0} value={times.end} onChange={(e) => setTimes({ ...times, end: e.target.value })} /></Field>
                  </div>
                  <button className="btn-ghost" disabled={busy === 'regen'} onClick={() => regenerate({ start: Number(times.start), end: Number(times.end) })}><RotateCcw size={15} />Re-render reel</button>
                  {a.reel?.excerpt && <p className="text-xs text-muted border-l-2 border-line pl-2 max-h-24 overflow-auto">{a.reel.excerpt}</p>}
                </div>
              )}

              {reviewable && (
                <div className="rounded-xl border border-line/70 p-3 space-y-3">
                  <p className="text-sm font-medium">Review this asset <span className="text-xs text-muted font-normal">(only this asset - the project is not affected)</span></p>
                  <input className="input" placeholder="Reason (required to reject or request a revision)" value={reason} onChange={(e) => setReason(e.target.value)} />
                  <div className="flex flex-wrap gap-2">
                    <button className="btn-ok" onClick={() => review('approve')} disabled={!!busy}><Check size={15} />Approve</button>
                    <button className="btn-danger" onClick={() => review('reject')} disabled={!!busy || !reason.trim()}><X size={15} />Reject</button>
                    <button className="btn-ghost" onClick={() => review('revise')} disabled={!!busy || !reason.trim()}><RotateCcw size={15} />Request revision</button>
                  </div>
                  <div className="flex gap-2">
                    <input className="input" placeholder="Add a comment…" value={comment} onChange={(e) => setComment(e.target.value)} />
                    <button className="btn-ghost" onClick={() => review('comment')} disabled={!comment.trim() || !!busy}><MessageSquare size={15} />Comment</button>
                  </div>
                </div>
              )}
              {a.reviews.length > 0 && (
                <ul className="space-y-1.5 text-sm">
                  {a.reviews.map((r) => (
                    <li key={r.id} className="flex gap-2 items-start"><StatusBadge status={r.action === 'comment' ? 'draft' : r.action} />
                      <span className="text-muted text-xs flex-1">{r.reason || r.comment || '-'}<br />{r.actor} · v{r.version} · {fmtDate(r.at)}</span></li>
                  ))}
                </ul>
              )}
            </>) : (
              <ul className="space-y-2">
                {[...a.versions].reverse().map((v) => (
                  <li key={v.version} className="rounded-xl border border-line/70 p-3 text-sm">
                    <div className="flex items-center gap-2 flex-wrap">
                      <b>v{v.version}</b>{v.version === a.version && <span className="chip bg-ok/15 text-ok border-ok/30">current</span>}
                      <span className="text-xs text-muted">{v.reason} · {v.created_by} · {fmtDate(v.created_at)}</span>
                      <span className="ml-auto flex gap-2">
                        {v.has_file && v.version !== a.version && <a className="btn-ghost !py-1" href={fileUrl.version(a.id, v.version)} target="_blank" rel="noreferrer">Open file</a>}
                        {v.version !== a.version && <button className="btn-ghost !py-1" onClick={() => run('restore', async () => setData(await api.post<Detail>(`/api/assets/${a.id}/versions/${v.version}/restore`)), 'Text restored as a new version')}>Restore text</button>}
                      </span>
                    </div>
                    <p className="text-xs text-muted mt-1 line-clamp-2">{v.title} - {v.caption}</p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </Modal>
  )
}
