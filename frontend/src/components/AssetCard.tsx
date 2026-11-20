import { Captions, FileAudio, FileText, Film, Image as ImageIcon, Languages, MessageSquareQuote, Play, Trash2, Video as VideoIcon } from 'lucide-react'
import { Asset, fileUrl } from '../api'
import { StatusBadge, fmtBytes, fmtDur, label } from '../ui'

export const KIND_LABEL: Record<string, string> = { creative: 'Creative',
  main_reel: 'Main reel', short_reel: 'Short reel', image_reel: 'Image reel', thumbnail: 'Thumbnail', carousel: 'Carousel slide', poster: 'Poster', social_post: 'Social post', subtitle: 'Subtitles',
  transcript: 'Transcript', caption: 'Captions', translation_video: 'Translation', translation_subtitle: 'Translated subtitles',
  audio: 'Audio', video: 'Source video',
}

export function KindIcon({ kind, size = 28 }: { kind: string; size?: number }) {
  const p = { size, strokeWidth: 1.5 }
  if (kind === 'main_reel' || kind === 'short_reel' || kind === 'image_reel') return <Film {...p} />
  if (kind === 'poster' || kind === 'thumbnail' || kind === 'carousel') return <ImageIcon {...p} />
  if (kind === 'social_post') return <MessageSquareQuote {...p} />
  if (kind === 'subtitle' || kind === 'translation_subtitle') return <Captions {...p} />
  if (kind === 'transcript' || kind === 'caption') return <FileText {...p} />
  if (kind === 'audio') return <FileAudio {...p} />
  if (kind === 'translation_video') return <Languages {...p} />
  return <VideoIcon {...p} />
}

export function Thumb({ a, className = '' }: { a: Asset; className?: string }) {
  const src = a.entity === 'video' ? fileUrl.videoThumb(a.id) : fileUrl.preview(a.id, a.version)
  const hasImg = a.entity === 'video' ? a.has_preview : a.has_preview
  const portrait = a.kind === 'main_reel' || a.kind === 'short_reel' || a.kind === 'image_reel'
  return (
    <div className={`relative overflow-hidden bg-surface2 flex items-center justify-center text-muted ${className}`}>
      {hasImg ? <img src={src} alt="" loading="lazy" className={`w-full h-full ${portrait ? 'object-cover' : 'object-cover'}`} /> : <KindIcon kind={a.kind} />}
      {(portrait || a.kind === 'translation_video' || a.kind === 'video') && (
        <span className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition bg-black/30"><Play size={30} className="text-white" /></span>
      )}
    </div>
  )
}

export const PART_LABEL: Record<string, string> = { opening: 'Opening line', idea: 'One clear idea', quotable: 'Scripture / quotable', emotion: 'Emotional moment', standalone: 'Works without context' }

/** "Message score" 0-100 of a reel: green 75+, amber 55+, grey below. Typed reels have no score. */
export function ScoreBadge({ a, className = '' }: { a: Asset; className?: string }) {
  const score = a.meta?.message_score as number | null | undefined
  if (a.kind !== 'main_reel' && a.kind !== 'short_reel') return null
  if (score == null) return a.meta?.typed ? <span className={`rounded-md border border-line bg-bg/80 px-1.5 py-0.5 text-[11px] text-muted ${className}`} title="Times typed by you - not scored">Typed</span> : null
  const tone = score >= 75 ? 'bg-ok/20 text-ok border-ok/40' : score >= 55 ? 'bg-warn/20 text-warn border-warn/40' : 'bg-muted/20 text-muted border-muted/40'
  const parts = a.meta?.score_parts as Record<string, number> | null | undefined
  const tip = [`Message score ${score}/100${a.meta?.score_source === 'estimated' ? ' (estimated from the transcript)' : ''}`,
    ...(parts ? Object.entries(parts).map(([k, v]) => `${PART_LABEL[k] ?? k}: ${v}/20`) : [])].join('\n')
  return <span className={`rounded-md border px-1.5 py-0.5 text-[11px] font-semibold tabular-nums ${tone} ${className}`} title={tip}>★ {score}</span>
}

export function reelScore(a: Asset): number { return typeof a.meta?.message_score === 'number' ? a.meta.message_score : -1 }

export default function AssetCard({ a, onOpen, selected, onSelect, onDelete, view = 'grid' }: {
  a: Asset; onOpen: () => void; selected?: boolean; onSelect?: (v: boolean) => void; onDelete?: () => void; view?: 'grid' | 'list'
}) {
  const trash = onDelete && (
    <button type="button" className="rounded-lg bg-bg/80 border border-line p-1.5 text-muted hover:text-danger hover:border-danger/60 transition"
      title="Delete" aria-label={`Delete ${a.title || 'item'}`} onClick={(e) => { e.stopPropagation(); onDelete() }}><Trash2 size={14} /></button>
  )
  const dur = a.meta?.duration ?? a.duration
  const meta = [KIND_LABEL[a.kind] || label(a.kind), dur ? fmtDur(dur) : '', a.size_bytes ? fmtBytes(a.size_bytes) : '', a.version > 1 ? `v${a.version}` : ''].filter(Boolean).join(' · ')
  const select = onSelect && (
    <input type="checkbox" checked={!!selected} onChange={(e) => onSelect(e.target.checked)} onClick={(e) => e.stopPropagation()}
      className="h-4 w-4 accent-[rgb(var(--primary))]" aria-label={`Select ${a.title}`} />
  )
  if (view === 'list') {
    return (
      <div className="card group flex items-center gap-3 p-2 pr-4 cursor-pointer hover:border-accent/50 transition" onClick={onOpen}>
        {select && <div className="pl-2">{select}</div>}
        <Thumb a={a} className="h-14 w-14 rounded-xl shrink-0" />
        <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{a.title || '(untitled)'}</p><p className="text-xs text-muted truncate">{a.project_name} · {meta}</p>
          {a.meta?.message_score != null && a.description && <p className="text-xs text-muted/80 truncate">{a.description}</p>}</div>
        <ScoreBadge a={a} />
        <StatusBadge status={a.status} />
        {trash}
      </div>
    )
  }
  return (
    <div className={`card group overflow-hidden cursor-pointer hover:border-accent/50 hover:-translate-y-0.5 transition ${selected ? 'ring-2 ring-primary' : ''}`} onClick={onOpen}>
      <div className="relative">
        <Thumb a={a} className={`w-full ${a.kind === 'main_reel' || a.kind === 'short_reel' || a.kind === 'image_reel' ? 'aspect-[9/12]' : a.kind === 'thumbnail' ? 'aspect-video' : a.kind === 'carousel' ? 'aspect-[4/5]' : 'aspect-[4/3]'}`} />
        {select && <div className="absolute top-2 left-2 bg-bg/70 rounded p-1">{select}</div>}
        <div className="absolute top-2 right-2"><StatusBadge status={a.status} /></div>
        <ScoreBadge a={a} className="absolute bottom-2 left-2" />
        {trash && <div className="absolute bottom-2 right-2">{trash}</div>}
      </div>
      <div className="p-3">
        <p className="text-sm font-medium line-clamp-2 min-h-[2.5rem]">{a.title || '(untitled)'}</p>
        <p className="text-xs text-muted mt-1 truncate">{[meta, a.meta?.category].filter(Boolean).join(' · ')}</p>
        {a.meta?.message_score != null && a.description && <p className="text-xs text-muted/80 mt-0.5 line-clamp-2" title={a.description}>{a.description}</p>}
        {a.project_name && <p className="text-xs text-muted/80 truncate">{a.project_name}</p>}
      </div>
    </div>
  )
}
