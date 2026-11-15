import { Captions, FileAudio, FileText, Film, Image as ImageIcon, Languages, MessageSquareQuote, Play, Video as VideoIcon } from 'lucide-react'
import { Asset, fileUrl } from '../api'
import { StatusBadge, fmtBytes, fmtDur, label } from '../ui'

export const KIND_LABEL: Record<string, string> = {
  main_reel: 'Main reel', short_reel: 'Short reel', image_reel: 'Image reel', poster: 'Poster', social_post: 'Social post', subtitle: 'Subtitles',
  transcript: 'Transcript', caption: 'Captions', translation_video: 'Translation', translation_subtitle: 'Translated subtitles',
  audio: 'Audio', video: 'Source video',
}

export function KindIcon({ kind, size = 28 }: { kind: string; size?: number }) {
  const p = { size, strokeWidth: 1.5 }
  if (kind === 'main_reel' || kind === 'short_reel' || kind === 'image_reel') return <Film {...p} />
  if (kind === 'poster') return <ImageIcon {...p} />
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

export default function AssetCard({ a, onOpen, selected, onSelect, view = 'grid' }: {
  a: Asset; onOpen: () => void; selected?: boolean; onSelect?: (v: boolean) => void; view?: 'grid' | 'list'
}) {
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
        <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{a.title || '(untitled)'}</p><p className="text-xs text-muted truncate">{a.project_name} · {meta}</p></div>
        <StatusBadge status={a.status} />
      </div>
    )
  }
  return (
    <div className={`card group overflow-hidden cursor-pointer hover:border-accent/50 hover:-translate-y-0.5 transition ${selected ? 'ring-2 ring-primary' : ''}`} onClick={onOpen}>
      <div className="relative">
        <Thumb a={a} className={`w-full ${a.kind === 'main_reel' || a.kind === 'short_reel' || a.kind === 'image_reel' ? 'aspect-[9/12]' : 'aspect-[4/3]'}`} />
        {select && <div className="absolute top-2 left-2 bg-bg/70 rounded p-1">{select}</div>}
        <div className="absolute top-2 right-2"><StatusBadge status={a.status} /></div>
      </div>
      <div className="p-3">
        <p className="text-sm font-medium line-clamp-2 min-h-[2.5rem]">{a.title || '(untitled)'}</p>
        <p className="text-xs text-muted mt-1 truncate">{meta}</p>
        {a.project_name && <p className="text-xs text-muted/80 truncate">{a.project_name}</p>}
      </div>
    </div>
  )
}
