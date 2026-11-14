import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Captions, Clapperboard, FileVideo, FolderPlus, Image as ImageIcon, Languages, Library, Sparkles, Upload, XCircle } from 'lucide-react'
import { api } from '../api'
import { Empty, ErrorBox, Loading, PageHeader, ProgressBar, StatusBadge, ago, fmtBytes, label, useApi } from '../ui'
import { NewProjectModal, RunJobModal } from '../components/Dialogs'
import ScrollBanner from '../components/ScrollBanner'

type D = { metrics: Record<string, number>; processing: any; recent: any; review_queue: Record<string, number> }

export default function Dashboard() {
  const { data, error, loading, reload } = useApi(() => api.get<D>('/api/dashboard'), [], 5000)
  const [modal, setModal] = useState<null | 'new' | 'upload' | 'generate' | 'translate' | 'reels' | 'poster'>(null)
  if (loading && !data) return <Loading rows={4} />
  if (error && !data) return <ErrorBox error={error} retry={reload} />
  const m = data!.metrics, p = data!.processing, r = data!.recent
  const used = m.storage_used_bytes, free = m.storage_available_bytes
  const cards: [string, number | string, string][] = [
    ['Projects', m.total_projects, 'text-accent'], ['Videos processed', m.videos_processed, 'text-primary'],
    ['Reels generated', m.reels_generated, 'text-teal'], ['Posters created', m.posters_created, 'text-accent'],
    ['Social posts', m.social_posts_created, 'text-primary'], ['Translations', m.translations_completed, 'text-teal'],
    ['Processing jobs', m.processing_jobs, 'text-accent'], ['Failed jobs', m.failed_jobs, m.failed_jobs ? 'text-danger' : 'text-muted'],
  ]
  const actions = [
    { l: 'New project', i: FolderPlus, k: 'new' }, { l: 'Upload video', i: Upload, k: 'upload' }, { l: 'Generate content', i: Sparkles, k: 'generate' },
    { l: 'Translate video', i: Languages, k: 'translate' }, { l: 'Create reels', i: Clapperboard, k: 'reels' }, { l: 'Create poster', i: ImageIcon, k: 'poster' },
  ] as const
  return (
    <>
      <ScrollBanner />
      <div className="relative z-10">
      <PageHeader title="Dashboard" subtitle="Everything here is calculated from your real project data." />
      <section className="card p-4 mb-6">
        <p className="text-sm font-medium mb-3">Quick actions</p>
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
          {actions.map(({ l, i: I, k }) => (
            <button key={k} className="btn-ghost flex-col !py-4 gap-1.5 hover:border-accent/60 hover:shadow-glow" onClick={() => setModal(k)}><I size={20} className="text-accent" />{l}</button>))}
          <Link to="/library" className="btn-ghost flex-col !py-4 gap-1.5 hover:border-accent/60 hover:shadow-glow"><Library size={20} className="text-accent" />Open library</Link>
        </div>
      </section>

      <section className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6" aria-label="Overview metrics">
        {cards.map(([t, v, c]) => (
          <div key={t} className="card p-4"><p className="text-xs text-muted">{t}</p><p className={`font-display text-3xl font-semibold mt-1 ${c}`}>{v}</p></div>))}
        <div className="card p-4 col-span-2">
          <div className="flex justify-between text-xs text-muted"><span>Storage consumed</span><span>Available</span></div>
          <div className="flex justify-between font-display text-xl mt-1"><span>{fmtBytes(used)}</span><span className="text-teal">{fmtBytes(free)}</span></div>
          <ProgressBar className="mt-2" value={(used / Math.max(1, used + free)) * 100} />
        </div>
      </section>

      <div className="grid lg:grid-cols-3 gap-6">
        <section className="card p-4 lg:col-span-1">
          <div className="flex justify-between items-center mb-3"><h2 className="font-display text-lg">Processing</h2><Link to="/processing" className="text-xs text-accent hover:underline">Open centre</Link></div>
          <div className="grid grid-cols-4 gap-2 text-center mb-4">
            {[['Active', p.active, 'text-accent'], ['Queued', p.queued, 'text-warn'], ['Done', p.completed, 'text-ok'], ['Failed', p.failed, 'text-danger']].map(([t, v, c]: any) => (
              <div key={t} className="rounded-xl bg-surface2/60 py-2"><p className={`font-display text-xl ${c}`}>{v}</p><p className="text-[11px] text-muted">{t}</p></div>))}
          </div>
          {p.paused && <p className="text-xs text-warn mb-2">The queue is paused.</p>}
          {p.running_jobs.length === 0 ? <p className="text-sm text-muted">No job is running right now.</p> : p.running_jobs.map((j: any) => (
            <div key={j.id} className="mb-3"><div className="flex justify-between text-xs mb-1"><span>{label(j.type)} <span className="text-muted">· {j.step}</span></span><span>{Math.round(j.progress)}%</span></div><ProgressBar value={j.progress} /></div>))}
        </section>

        <section className="card p-4 lg:col-span-2">
          <h2 className="font-display text-lg mb-3">Recent activity</h2>
          {m.total_projects === 0 && r.uploads.length === 0 ? (
            <Empty icon={<FileVideo size={34} />} title="Nothing here yet" hint="Create a project and upload a video to get started." action={<button className="btn-primary" onClick={() => setModal('new')}>Create your first project</button>} />
          ) : (
            <div className="grid md:grid-cols-2 gap-5 text-sm">
              <Feed title="Recently uploaded" empty="No videos yet" rows={r.uploads.map((u: any) => ({ k: `u${u.id}`, to: `/projects/${u.project_id}`, a: u.name, b: u.project, t: u.at }))} />
              <Feed title="Completed projects" empty="No finished projects yet" rows={r.completed_projects.map((u: any) => ({ k: `p${u.id}`, to: `/projects/${u.id}`, a: u.name, badge: u.status, t: u.at }))} />
              <Feed title="Generated content" empty="Nothing generated yet" rows={r.generated.map((u: any) => ({ k: `g${u.id}`, to: `/review`, a: u.title || label(u.kind), b: `${label(u.kind)} · ${u.project}`, t: u.at }))} />
              <Feed title="Recent approvals" empty="No reviews yet" rows={r.approvals.map((u: any, i: number) => ({ k: `a${i}`, to: `/review`, a: `Asset #${u.asset_id}`, badge: u.action, b: u.reason, t: u.at }))} />
              <div className="md:col-span-2"><Feed title="Failed processing jobs" empty="No failures 🎉" danger rows={r.failed_jobs.map((u: any) => ({ k: `f${u.id}`, to: '/processing', a: `${label(u.type)} (job #${u.id})`, b: u.error, t: u.at }))} /></div>
            </div>)}
        </section>
      </div>
      </div>
      {(modal === 'new' || modal === 'upload') &&<NewProjectModal onClose={() => { setModal(null); reload() }} withUpload />}
      {(modal === 'generate' || modal === 'translate' || modal === 'reels' || modal === 'poster') && <RunJobModal mode={modal} onClose={() => { setModal(null); reload() }} />}
    </>
  )
}

function Feed({ title, rows, empty, danger }: { title: string; empty: string; danger?: boolean; rows: { k: string; to: string; a: string; b?: string; badge?: string; t: string }[] }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wider text-muted mb-2">{title}</p>
      {rows.length === 0 ? <p className="text-muted text-sm">{empty}</p> : (
        <ul className="space-y-1.5">{rows.map((r) => (
          <li key={r.k}><Link to={r.to} className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-surface2/70">
            {danger && <XCircle size={14} className="text-danger shrink-0" />}
            <span className="min-w-0 flex-1"><span className="block truncate">{r.a}</span>{r.b && <span className="block truncate text-xs text-muted">{r.b}</span>}</span>
            {r.badge && <StatusBadge status={r.badge} />}<span className="text-xs text-muted shrink-0">{ago(r.t)}</span></Link></li>))}</ul>)}
    </div>
  )
}
