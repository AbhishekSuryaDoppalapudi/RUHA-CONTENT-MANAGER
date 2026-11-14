import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Brain, Download, Lightbulb, Plus, Search, Trash2 } from 'lucide-react'
import { api, qs } from '../api'
import { Empty, ErrorBox, Loading, PageHeader, Spinner, Tabs, fmtDate, label, useApi, useConfirm, useToast } from '../ui'

export default function MemoryPage() {
  const toast = useToast(), confirm = useConfirm()
  const [tab, setTab] = useState('search')
  const stats = useApi(() => api.get<any>('/api/memory/stats'), [])
  return (
    <>
      <PageHeader title="AI memory" subtitle="What El Ruach remembers locally: transcripts, themes, captions, your approvals and preferences. Retrieval only - no model is trained." actions={<>
        <a className="btn-ghost" href="/api/memory/export"><Download size={15} />Export</a></>} />
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2 mb-5">
        {['transcript', 'summary', 'theme', 'quote', 'caption', 'post', 'preference'].map((k) => (
          <div key={k} className="card p-3"><p className="text-xs text-muted">{label(k)}s</p><p className="font-display text-2xl">{stats.data?.by_kind?.[k] ?? 0}</p></div>))}
        <div className="card p-3"><p className="text-xs text-muted">Embedding</p><p className="text-sm font-medium truncate" title="hash = offline lexical embedding; install an Ollama embedding model for semantic search">{stats.data?.embedder ?? '-'}</p></div>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'search', label: 'Search' }, { id: 'topics', label: 'Topics' }, { id: 'prefs', label: 'Preferences' }, { id: 'inspect', label: 'Inspect & delete' }]} />
      <div className="mt-5">
        {tab === 'search' && <SearchTab />}
        {tab === 'topics' && <TopicsTab />}
        {tab === 'prefs' && <PrefsTab onChange={stats.reload} />}
        {tab === 'inspect' && <InspectTab onChange={stats.reload} />}
      </div>
    </>
  )
}

function SearchTab() {
  const [q, setQ] = useState(''); const [kinds, setKinds] = useState(''); const [res, setRes] = useState<any>(null); const [busy, setBusy] = useState(false)
  const toast = useToast()
  const go = async () => { if (!q.trim()) return; setBusy(true); try { setRes(await api.get(`/api/memory/search${qs({ q, kinds, n: 15 })}`)) } catch (e) { toast.err(e) } finally { setBusy(false) } }
  return (
    <div className="space-y-4">
      <div className="card p-3 flex gap-2 flex-wrap">
        <div className="relative flex-1 min-w-[240px]"><Search size={15} className="absolute left-3 top-2.5 text-muted" /><input className="input !pl-9" placeholder="Search previous sermons, quotes, captions…  e.g. “forgiveness”" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && go()} /></div>
        <select className="input !w-auto" value={kinds} onChange={(e) => setKinds(e.target.value)}><option value="">Everything</option><option value="transcript">Transcripts</option><option value="summary">Summaries</option><option value="quote">Quotes</option><option value="caption,post">Captions & posts</option></select>
        <button className="btn-primary" onClick={go} disabled={busy}>{busy ? <Spinner /> : <Search size={15} />}Search</button>
      </div>
      {!res ? <Empty icon={<Brain size={34} />} title="Search everything you've produced" hint="Find where a topic was already covered, retrieve earlier captions, or check whether similar content exists." />
        : res.hits.length === 0 ? <Empty title="No matches" hint="Process a video first so there is something to remember." /> : (
        <div className="space-y-2">{res.hits.map((h: any) => (
          <div key={h.vector_id} className="card p-3"><div className="flex items-center gap-2 mb-1 flex-wrap"><span className="chip border-accent/40 text-accent">{h.kind}</span>
            {h.project_id && <Link to={`/projects/${h.project_id}`} className="text-sm text-accent hover:underline">{h.project_name}</Link>}
            {h.meta?.outcome && h.meta.outcome !== 'draft' && <span className="chip border-line text-muted">{h.meta.outcome}</span>}
            <span className="ml-auto text-xs text-muted">match {(h.score * 100).toFixed(0)}%</span></div><p className="text-sm whitespace-pre-wrap">{h.text}</p></div>))}
          <p className="text-xs text-muted">Embedding: {res.embedder}</p></div>)}
    </div>)
}

function TopicsTab() {
  const { data, loading, error, reload } = useApi(() => api.get<any>('/api/memory/topics'), [])
  if (loading && !data) return <Loading />
  if (error) return <ErrorBox error={error} retry={reload} />
  return (
    <div className="grid md:grid-cols-2 gap-5">
      <div className="card p-4"><h2 className="font-display text-lg mb-3">Topics already covered</h2>
        {data.covered.length === 0 ? <p className="text-sm text-muted">No themes stored yet. They are extracted when a video is analysed.</p> :
          <ul className="space-y-2">{data.covered.map((c: any) => <li key={c.topic} className="flex items-center gap-2 text-sm"><span className="capitalize">{c.topic}</span><span className="chip border-line text-muted">{c.count}×</span><span className="ml-auto text-xs text-muted truncate">{c.example_project}</span></li>)}</ul>}</div>
      <div className="card p-4"><h2 className="font-display text-lg mb-1 flex items-center gap-2"><Lightbulb size={18} className="text-warn" />Unexplored topics</h2>
        <p className="text-xs text-muted mb-3">{data.recommendations.basis}</p>
        <div className="flex flex-wrap gap-2">{data.recommendations.suggestions.map((s: string) => <span key={s} className="chip !text-sm border-teal/40 text-teal">{s}</span>)}</div></div>
    </div>)
}

function PrefsTab({ onChange }: { onChange: () => void }) {
  const toast = useToast(), confirm = useConfirm()
  const [text, setText] = useState('')
  const { data, reload, loading } = useApi(() => api.get<any>('/api/memory/items?kind=preference&limit=100'), [])
  const add = async () => { if (!text.trim()) return; try { await api.post('/api/memory/preferences', { text }); setText(''); toast.ok('Preference remembered'); reload(); onChange() } catch (e) { toast.err(e) } }
  return (
    <div className="max-w-3xl space-y-4">
      <p className="text-sm text-muted">Tell El Ruach about your style and brand voice. These notes are added to every content-planning prompt.</p>
      <div className="flex gap-2"><input className="input" placeholder="e.g. Always keep captions warm, under 120 characters, with no emojis" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()} />
        <button className="btn-primary" onClick={add}><Plus size={15} />Add</button></div>
      {loading ? <Loading rows={2} /> : data.items.length === 0 ? <Empty title="No preferences yet" /> : (
        <ul className="space-y-2">{data.items.map((i: any) => <li key={i.id} className="card p-3 flex items-center gap-3 text-sm">{i.text}
          <button className="btn-ghost !p-1.5 ml-auto" aria-label="Forget" onClick={async () => { await api.post('/api/memory/delete', { ids: [i.id] }); reload(); onChange() }}><Trash2 size={14} /></button></li>)}</ul>)}
    </div>)
}

function InspectTab({ onChange }: { onChange: () => void }) {
  const toast = useToast(), confirm = useConfirm()
  const [kind, setKind] = useState(''); const [q, setQ] = useState('')
  const { data, loading, error, reload } = useApi(() => api.get<any>(`/api/memory/items${qs({ kind, q, limit: 100 })}`), [kind, q])
  const del = async (body: any, title: string, requireText?: string) => {
    if (!(await confirm({ title, body: 'Deleted memory cannot be recovered (generated files are not affected).', confirm: 'Delete', danger: true, requireText }))) return
    try { const r = await api.post<{ deleted: number }>('/api/memory/delete', body); toast.ok(`${r.deleted} memory item(s) deleted`); reload(); onChange() } catch (e) { toast.err(e) }
  }
  return (
    <div className="space-y-3">
      <div className="flex gap-2 flex-wrap"><select className="input !w-auto" value={kind} onChange={(e) => setKind(e.target.value)}><option value="">All kinds</option>{['transcript', 'summary', 'theme', 'quote', 'caption', 'post', 'preference'].map((k) => <option key={k}>{k}</option>)}</select>
        <input className="input !w-64" placeholder="Filter text…" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn-ghost" onClick={() => api.post('/api/memory/reindex').then(() => toast.ok('Vectors rebuilt'))} title="Rebuild vectors from stored text">Rebuild index</button>
        {kind && <button className="btn-danger" onClick={() => del({ kinds: [kind] }, `Delete all “${kind}” memory?`)}>Delete all {kind}</button>}
        <button className="btn-danger ml-auto" onClick={() => del({ everything: true }, 'Erase ALL AI memory?', 'ERASE')}>Erase everything</button></div>
      {loading && !data ? <Loading /> : error ? <ErrorBox error={error} retry={reload} /> : data.items.length === 0 ? <Empty title="Memory is empty" /> : (
        <div className="card divide-y divide-line/50">{data.items.map((i: any) => (
          <div key={i.id} className="p-3 flex gap-3 items-start"><span className="chip border-line text-muted shrink-0">{i.kind}</span>
            <div className="min-w-0 flex-1"><p className="text-sm line-clamp-3 whitespace-pre-wrap">{i.text}</p><p className="text-xs text-muted">{fmtDate(i.created_at)}{i.project_id ? ` · project ${i.project_id}` : ''}</p></div>
            <button className="btn-ghost !p-1.5" aria-label="Delete" onClick={() => del({ ids: [i.id] }, 'Delete this memory item?')}><Trash2 size={14} /></button></div>))}</div>)}
      <p className="text-xs text-muted">Showing up to 100 of {data?.total ?? 0}.</p>
    </div>)
}
