import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Download, LayoutGrid, List, Search, X } from 'lucide-react'
import { api, Asset, qs } from '../api'
import { Empty, ErrorBox, Loading, PageHeader, useApi, useConfirm, useDebounced, useToast, fmtBytes } from '../ui'
import AssetCard from '../components/AssetCard'
import AssetDetail from '../components/AssetDetail'

const TYPES = [['', 'Everything'], ['video', 'Videos'], ['reel', 'Main reels'], ['short', 'Short reels'], ['image', 'Image reels'], ['poster', 'Posters'], ['social', 'Social graphics'], ['transcript', 'Transcripts'],
  ['subtitle', 'Subtitles'], ['audio', 'Audio'], ['translation', 'Translations']]

export default function LibraryPage() {
  const toast = useToast(), confirm = useConfirm()
  const [type, setType] = useState('')
  const [q, setQ] = useState(''); const dq = useDebounced(q)
  const [tag, setTag] = useState('')
  const [status, setStatus] = useState('')
  const [sp] = useSearchParams()
  const [project, setProject] = useState(sp.get('project') || '')
  const projects = useApi(() => api.get<{ items: { id: number; name: string }[] }>('/api/projects?limit=200'), [])
  const [sort, setSort] = useState('created'); const [order, setOrder] = useState('desc')
  const [view, setView] = useState<'grid' | 'list'>(() => (localStorage.getItem('ruha-lib-view') as any) || 'grid')
  const [sel, setSel] = useState<Record<string, boolean>>({})
  const [open, setOpen] = useState<number | null>(null)
  const [dl, setDl] = useState(false)
  const { data, error, loading, reload } = useApi(() => api.get<{ total: number; items: Asset[] }>(`/api/library${qs({ type, q: dq, tag, status, project_id: project, sort, order, limit: 200 })}`), [type, dq, tag, status, project, sort, order])
  const key = (a: Asset) => `${a.entity}:${a.id}`
  const chosen = (data?.items ?? []).filter((a) => sel[key(a)])

  const download = async () => {
    setDl(true)
    try {
      const res = await fetch('/api/library/download', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ asset_ids: chosen.filter((a) => a.entity === 'asset').map((a) => a.id), video_ids: chosen.filter((a) => a.entity === 'video').map((a) => a.id) }) })
      if (!res.ok) throw new Error((await res.json()).detail || 'Download failed')
      const url = URL.createObjectURL(await res.blob())
      const a = document.createElement('a'); a.href = url; a.download = 'ruha-library.zip'; a.click(); URL.revokeObjectURL(url)
      toast.ok(`Downloaded ${chosen.length} item(s)`)
    } catch (e) { toast.err(e) } finally { setDl(false) }
  }
  const setViewMode = (v: 'grid' | 'list') => { setView(v); localStorage.setItem('ruha-lib-view', v) }

  return (
    <>
      <PageHeader title="Content library" subtitle="Every video, reel, poster, transcript and translation in one place." actions={<>
        <button className={view === 'grid' ? 'btn-primary' : 'btn-ghost'} onClick={() => setViewMode('grid')} aria-label="Grid view" title="Grid view"><LayoutGrid size={16} /></button>
        <button className={view === 'list' ? 'btn-primary' : 'btn-ghost'} onClick={() => setViewMode('list')} aria-label="List view" title="List view"><List size={16} /></button></>} />
      <div className="flex flex-wrap gap-1.5 mb-4">{TYPES.map(([v, l]) => <button key={v} onClick={() => setType(v)} className={`chip !px-3 !py-1 ${type === v ? 'bg-primary/25 border-primary/60 text-ink' : 'border-line text-muted hover:text-ink'}`}>{l}</button>)}</div>
      <div className="card p-3 mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-6">
        <div className="relative lg:col-span-2"><Search size={15} className="absolute left-3 top-2.5 text-muted" /><input className="input !pl-9" placeholder="Search titles, captions, descriptions…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search library" /></div>
        <select className="input" value={project} onChange={(e) => setProject(e.target.value)} aria-label="Filter by project"><option value="">All projects</option>{(projects.data?.items ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
        <input className="input" placeholder="Filter by tag" value={tag} onChange={(e) => setTag(e.target.value)} aria-label="Filter by tag" />
        <select className="input" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by review status"><option value="">Any review status</option><option value="draft">Draft</option><option value="approved">Approved</option><option value="rejected">Rejected</option><option value="revision_requested">Revision requested</option></select>
        <div className="flex gap-2"><select className="input" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort"><option value="created">Newest</option><option value="name">Name</option><option value="size">Size</option><option value="updated">Updated</option></select>
          <button className="btn-ghost" onClick={() => setOrder(order === 'desc' ? 'asc' : 'desc')} title="Toggle order">{order === 'desc' ? '↓' : '↑'}</button></div>
      </div>
      {chosen.length > 0 && (
        <div className="card p-3 mb-4 flex items-center gap-3 sticky top-16 z-20 border-primary/50">
          <b className="text-sm">{chosen.length} selected</b><span className="text-xs text-muted">{fmtBytes(chosen.reduce((s, a) => s + (a.size_bytes || 0), 0))}</span>
          <button className="btn-primary ml-auto" onClick={download} disabled={dl}><Download size={15} />{dl ? 'Preparing zip…' : 'Download selected'}</button>
          <button className="btn-ghost" onClick={() => setSel({})}><X size={15} />Clear</button>
        </div>)}
      {loading && !data ? <Loading rows={4} /> : error && !data ? <ErrorBox error={error} retry={reload} /> : data!.items.length === 0 ? (
        <Empty title="Nothing in the library" hint={dq || tag || status || type || project ? 'No items match these filters.' : 'Generated content and uploaded videos will show up here.'} />
      ) : (
        <>
          <div className="flex items-center gap-3 mb-2 text-xs text-muted"><label className="flex items-center gap-2"><input type="checkbox" className="accent-[rgb(var(--primary))]" checked={chosen.length === data!.items.length}
            onChange={(e) => setSel(e.target.checked ? Object.fromEntries(data!.items.map((a) => [key(a), true])) : {})} />Select all</label><span>{data!.total} item(s)</span></div>
          <div className={view === 'grid' ? 'grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4' : 'space-y-2'}>
            {data!.items.map((a) => <AssetCard key={key(a)} a={a} view={view} selected={!!sel[key(a)]} onSelect={(v) => setSel({ ...sel, [key(a)]: v })}
              onDelete={a.entity === 'video' ? undefined : async () => {
                if (!(await confirm({ title: `Delete “${a.title || 'this item'}”?`, body: 'The file and all its versions are removed from disk. This cannot be undone.', confirm: 'Delete', danger: true }))) return
                try { await api.del(`/api/assets/${a.id}`); toast.ok('Deleted'); reload() } catch (e) { toast.err(e) }
              }}
              onOpen={() => a.entity === 'video' ? window.open(`/api/videos/${a.id}/stream`, '_blank') : setOpen(a.id)} />)}
          </div>
        </>)}
      {open && <AssetDetail id={open} onClose={() => setOpen(null)} onChanged={reload} />}
    </>
  )
}
