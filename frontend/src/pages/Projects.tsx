import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Archive, ArchiveRestore, Copy, FolderPlus, Search, Trash2, Video } from 'lucide-react'
import { api, fileUrl, Project, PROJECT_STATUSES, qs } from '../api'
import { Empty, ErrorBox, Loading, PageHeader, StatusBadge, ago, fmtDur, label, useApi, useConfirm, useDebounced, useToast } from '../ui'
import { NewProjectModal } from '../components/Dialogs'

export default function Projects() {
  const toast = useToast(), confirm = useConfirm()
  const [sp, setSp] = useSearchParams()
  const [q, setQ] = useState('')
  const dq = useDebounced(q)
  const [f, setF] = useState({ status: '', content_type: '', sort: 'created', order: 'desc', created_from: '', created_to: '', include_archived: false })
  const [show, setShow] = useState(sp.get('new') === '1')
  const { data, error, loading, reload } = useApi(() => api.get<{ total: number; items: Project[] }>(`/api/projects${qs({ q: dq, ...f })}`), [dq, JSON.stringify(f)], 6000)

  const act = async (fn: () => Promise<any>, ok: string) => { try { await fn(); toast.ok(ok); reload() } catch (e) { toast.err(e) } }
  const remove = async (p: Project) => {
    if (await confirm({ title: `Delete “${p.name}”?`, body: 'The project, its video and every generated file are permanently deleted. This cannot be undone.', confirm: 'Delete forever', danger: true, requireText: p.name }))
      act(() => api.del(`/api/projects/${p.id}?confirm=true`), 'Project deleted')
  }
  return (
    <>
      <PageHeader title="Projects" subtitle="Each project has its own workspace with every related asset." actions={<button className="btn-primary" onClick={() => setShow(true)}><FolderPlus size={16} />New project</button>} />
      <div className="card p-3 mb-5 grid gap-2 sm:grid-cols-2 lg:grid-cols-7">
        <div className="relative lg:col-span-2"><Search size={15} className="absolute left-3 top-2.5 text-muted" /><input className="input !pl-9" placeholder="Search name, description, tag…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search projects" /></div>
        <select className="input" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })} aria-label="Filter by status"><option value="">All statuses</option>{PROJECT_STATUSES.map((s) => <option key={s} value={s}>{label(s)}</option>)}</select>
        <select className="input" value={f.content_type} onChange={(e) => setF({ ...f, content_type: e.target.value })} aria-label="Filter by content type"><option value="">All content types</option>{['sermon', 'teaching', 'interview', 'event', 'testimony', 'other'].map((s) => <option key={s}>{s}</option>)}</select>
        <input type="date" className="input" value={f.created_from} onChange={(e) => setF({ ...f, created_from: e.target.value })} aria-label="Created from" title="Created from" />
        <input type="date" className="input" value={f.created_to} onChange={(e) => setF({ ...f, created_to: e.target.value })} aria-label="Created to" title="Created to" />
        <div className="flex gap-2"><select className="input" value={f.sort} onChange={(e) => setF({ ...f, sort: e.target.value })} aria-label="Sort by"><option value="created">Created</option><option value="updated">Updated</option><option value="name">Name</option><option value="status">Status</option></select>
          <button className="btn-ghost" onClick={() => setF({ ...f, order: f.order === 'desc' ? 'asc' : 'desc' })} title="Toggle sort order">{f.order === 'desc' ? '↓' : '↑'}</button></div>
      </div>
      {loading && !data ? <Loading rows={4} /> : error && !data ? <ErrorBox error={error} retry={reload} /> : data!.items.length === 0 ? (
        <Empty icon={<Video size={36} />} title={q || f.status || f.content_type ? 'No projects match these filters' : 'No projects yet'} hint="A project holds one source video and everything generated from it." action={<button className="btn-primary" onClick={() => setShow(true)}>Create a project</button>} />
      ) : (
        <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {data!.items.map((p) => (
            <div key={p.id} className="card overflow-hidden group hover:border-accent/50 transition flex flex-col">
              <Link to={`/projects/${p.id}`} className="block relative aspect-video bg-surface2">
                {p.has_thumbnail ? <img src={`${fileUrl.projectThumb(p.id)}?t=${p.updated_at}`} alt="" loading="lazy" className="w-full h-full object-cover" /> : <div className="h-full flex items-center justify-center text-muted"><Video size={34} strokeWidth={1.3} /></div>}
                <div className="absolute top-2 right-2"><StatusBadge status={p.status} /></div>
                {p.video && <span className="absolute bottom-2 right-2 chip bg-black/60 border-white/10 text-white">{fmtDur(p.video.duration)}</span>}
              </Link>
              <div className="p-4 flex-1 flex flex-col gap-2">
                <Link to={`/projects/${p.id}`} className="font-display text-lg font-semibold leading-tight hover:text-accent line-clamp-1">{p.name}</Link>
                <p className="text-sm text-muted line-clamp-2 min-h-[2.5rem]">{p.description || 'No description'}</p>
                <div className="flex flex-wrap gap-1">{p.tags.map((t) => <span key={t} className="chip border-line text-muted">#{t}</span>)}</div>
                <p className="text-xs text-muted mt-auto">{label(p.content_type)} · {Object.values(p.asset_counts).reduce((a, b) => a + b, 0)} assets · created {ago(p.created_at)}</p>
                <div className="flex gap-1 pt-1 border-t border-line/50">
                  <button className="btn-ghost !px-2 !py-1.5" title="Duplicate (copies the video, not the generated files)" onClick={() => act(() => api.post(`/api/projects/${p.id}/duplicate`), 'Project duplicated')}><Copy size={15} /></button>
                  {p.status === 'archived'
                    ? <button className="btn-ghost !px-2 !py-1.5" title="Restore from archive" onClick={() => act(() => api.post(`/api/projects/${p.id}/unarchive`), 'Project restored')}><ArchiveRestore size={15} /></button>
                    : <button className="btn-ghost !px-2 !py-1.5" title="Archive" onClick={() => act(() => api.post(`/api/projects/${p.id}/archive`), 'Project archived')}><Archive size={15} /></button>}
                  <button className="btn-danger !px-2 !py-1.5 ml-auto" title="Delete project" onClick={() => remove(p)}><Trash2 size={15} /></button>
                </div>
              </div>
            </div>))}
        </div>)}
      {show && <NewProjectModal onClose={() => { setShow(false); setSp({}); reload() }} />}
    </>
  )
}
