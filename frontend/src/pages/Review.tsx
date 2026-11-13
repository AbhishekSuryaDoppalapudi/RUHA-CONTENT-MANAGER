import { useState } from 'react'
import { CheckCircle2 } from 'lucide-react'
import { api, Asset, qs } from '../api'
import { Empty, ErrorBox, Loading, PageHeader, Tabs, useApi } from '../ui'
import AssetCard from '../components/AssetCard'
import AssetDetail from '../components/AssetDetail'

const TABS = [['draft', 'Needs review'], ['revision_requested', 'Revision requested'], ['approved', 'Approved'], ['rejected', 'Rejected']]

export default function Review() {
  const [tab, setTab] = useState('draft')
  const [open, setOpen] = useState<number | null>(null)
  const [kind, setKind] = useState('')
  const counts = useApi(() => api.get<{ review_queue: Record<string, number> }>('/api/dashboard'), [], 8000)
  const { data, error, loading, reload } = useApi(() => api.get<{ items: Asset[] }>(`/api/library${qs({ status: tab, limit: 300, sort: 'created', order: 'desc' })}`), [tab])
  const items = (data?.items ?? []).filter((a) => ['main_reel', 'short_reel', 'poster', 'social_post'].includes(a.kind)).filter((a) => !kind || a.kind === kind)
  const q = counts.data?.review_queue ?? {}
  return (
    <>
      <PageHeader title="Review" subtitle="Approve, reject or request changes - one asset at a time. Approving an asset never approves the whole project." />
      <Tabs value={tab} onChange={setTab} tabs={TABS.map(([id, l]) => ({ id, label: l, count: q[id] ?? 0 }))} />
      <div className="flex gap-1.5 my-4 flex-wrap">{[['', 'All types'], ['main_reel', 'Main reels'], ['short_reel', 'Short reels'], ['poster', 'Posters'], ['social_post', 'Social posts']].map(([v, l]) => (
        <button key={v} onClick={() => setKind(v)} className={`chip !px-3 !py-1 ${kind === v ? 'bg-primary/25 border-primary/60' : 'border-line text-muted hover:text-ink'}`}>{l}</button>))}</div>
      {loading && !data ? <Loading rows={3} /> : error && !data ? <ErrorBox error={error} retry={reload} /> : items.length === 0 ? (
        <Empty icon={<CheckCircle2 size={36} />} title={tab === 'draft' ? 'Nothing waiting for review' : 'Nothing here'} hint={tab === 'draft' ? 'Newly generated and edited assets appear here for approval.' : undefined} />
      ) : <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4">{items.map((a) => <AssetCard key={a.id} a={a} onOpen={() => setOpen(a.id)} />)}</div>}
      {open && <AssetDetail id={open} onClose={() => setOpen(null)} onChanged={() => { reload(); counts.reload() }} />}
    </>
  )
}
