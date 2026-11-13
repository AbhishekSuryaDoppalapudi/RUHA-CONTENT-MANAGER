import { useState } from 'react'
import { Download } from 'lucide-react'
import { api } from '../api'
import { Empty, ErrorBox, Loading, PageHeader, ProgressBar, fmtBytes, label, useApi } from '../ui'

export default function Analytics() {
  const [days, setDays] = useState(90)
  const { data: d, error, loading, reload } = useApi(() => api.get<any>(`/api/analytics?days=${days}`), [days])
  if (loading && !d) return <Loading rows={3} />
  if (error && !d) return <ErrorBox error={error} retry={reload} />
  const a = d.assets, total = a.total
  const maxTopic = Math.max(1, ...d.topics.map((t: any) => t.count))
  const cat = Object.entries(d.category_distribution) as [string, number][]
  const catTotal = cat.reduce((s, [, v]) => s + v, 0) || 1
  const growth = d.storage_growth as { date: string; cumulative_bytes: number }[]
  const maxG = Math.max(1, ...growth.map((g) => g.cumulative_bytes))
  const pt = Object.entries(d.production_time) as [string, any][]
  const fails = Object.entries(d.processing_failures) as [string, number][]
  return (
    <>
      <PageHeader title="Analytics" subtitle="Production statistics computed from your own records." actions={<>
        <select className="input !w-auto" value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Time window">{[7, 30, 90, 365].map((n) => <option key={n} value={n}>Last {n} days</option>)}</select>
        <a className="btn-ghost" href={`/api/analytics/export?format=csv&days=${days}`}><Download size={15} />CSV</a>
        <a className="btn-ghost" href={`/api/analytics/export?format=json&days=${days}`}><Download size={15} />JSON</a></>} />
      <div className="grid grid-cols-2 lg:grid-cols-6 gap-3 mb-5">
        {[['Generated assets', total], ['Approved', a.approved], ['Rejected', a.rejected], ['Awaiting review', a.draft + a.revision_requested], ['Revision requests', d.revisions.revision_requests], ['Videos processed', d.videos_processed]].map(([t, v]) => (
          <div key={t as string} className="card p-4"><p className="text-xs text-muted">{t}</p><p className="font-display text-3xl">{v}</p></div>))}
      </div>
      <div className="grid lg:grid-cols-2 gap-5">
        <section className="card p-4"><h2 className="font-display text-lg mb-3">Review outcome</h2>
          {total === 0 ? <Empty title="No assets yet" /> : (['approved', 'draft', 'revision_requested', 'rejected'] as const).map((k) => (
            <div key={k} className="mb-3"><div className="flex justify-between text-sm mb-1"><span>{label(k)}</span><span className="text-muted">{a[k]} · {Math.round((a[k] / total) * 100)}%</span></div><ProgressBar value={(a[k] / total) * 100} /></div>))}
          <p className="text-xs text-muted">{d.revisions.extra_versions} extra version(s) created by edits and regenerations.</p></section>
        <section className="card p-4"><h2 className="font-display text-lg mb-3">Content categories</h2>
          {cat.length === 0 ? <Empty title="No content yet" /> : cat.map(([k, v]) => <div key={k} className="mb-3"><div className="flex justify-between text-sm mb-1"><span>{label(k)}</span><span className="text-muted">{v}</span></div><ProgressBar value={(v / catTotal) * 100} /></div>)}</section>
        <section className="card p-4"><h2 className="font-display text-lg mb-3">Most frequent topics</h2>
          {d.topics.length === 0 ? <Empty title="No topics yet" hint="Themes are extracted when videos are analysed." /> : d.topics.map((t: any) => (
            <div key={t.topic} className="flex items-center gap-3 mb-2 text-sm"><span className="w-32 truncate capitalize">{t.topic}</span><div className="flex-1"><ProgressBar value={(t.count / maxTopic) * 100} /></div><span className="text-muted w-6 text-right">{t.count}</span></div>))}</section>
        <section className="card p-4"><h2 className="font-display text-lg mb-3">Production time</h2>
          {pt.length === 0 ? <Empty title="No completed jobs in this window" /> : <table className="w-full text-sm"><thead className="text-xs text-muted text-left"><tr><th className="pb-2">Job</th><th>Runs</th><th>Total</th><th>Average</th></tr></thead>
            <tbody>{pt.map(([k, v]) => <tr key={k} className="border-t border-line/50"><td className="py-1.5">{label(k)}</td><td>{v.count}</td><td>{Math.round(v.total_seconds)}s</td><td>{Math.round(v.avg_seconds)}s</td></tr>)}</tbody></table>}
          <h3 className="font-display mt-4 mb-2">Processing failures</h3>
          {fails.length === 0 ? <p className="text-sm text-muted">None in this window.</p> : fails.map(([k, v]) => <p key={k} className="text-sm flex justify-between"><span>{label(k)}</span><b className="text-danger">{v}</b></p>)}</section>
        <section className="card p-4 lg:col-span-2"><h2 className="font-display text-lg mb-3">Storage growth</h2>
          {growth.length === 0 ? <Empty title="No files yet" /> : (<>
            <svg viewBox="0 0 600 140" className="w-full h-40" role="img" aria-label="Storage growth over time">
              <polyline fill="none" stroke="rgb(var(--accent))" strokeWidth="2.5" points={growth.map((g, i) => `${growth.length === 1 ? 300 : (i / (growth.length - 1)) * 580 + 10},${130 - (g.cumulative_bytes / maxG) * 115}`).join(' ')} />
              {growth.map((g, i) => <circle key={g.date} cx={growth.length === 1 ? 300 : (i / (growth.length - 1)) * 580 + 10} cy={130 - (g.cumulative_bytes / maxG) * 115} r="3.5" fill="rgb(var(--primary))"><title>{g.date}: {fmtBytes(g.cumulative_bytes)}</title></circle>)}</svg>
            <p className="text-xs text-muted">{growth[0].date} → {growth[growth.length - 1].date} · now {fmtBytes(maxG)} of generated content and source videos</p></>)}</section>
      </div>
      <p className="text-xs text-muted mt-5">{d.external_metrics.note}</p>
    </>)
}
