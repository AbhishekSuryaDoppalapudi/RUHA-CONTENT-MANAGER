import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { fmt, search, Sentence } from '../reelLogic'

/** Browse the sermon's transcript with times and pick a range: click the first sentence, then the last one, then add it as a line
 *  of the "your times and comments" box (start-end main|short - comment). */
export default function TranscriptPicker({ pid, onAdd }: { pid: number; onAdd: (line: string) => void }) {
  const [sentences, setSentences] = useState<Sentence[] | null>(null)
  const [err, setErr] = useState('')
  const [q, setQ] = useState('')
  const [a, setA] = useState<number | null>(null), [b, setB] = useState<number | null>(null)   // indexes of the first and last chosen sentence
  const [kind, setKind] = useState<'main' | 'short'>('short')
  const [note, setNote] = useState('')
  const list = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setSentences(null); setErr(''); setA(null); setB(null)
    api.get<{ sentences: Sentence[] }>(`/api/sermons/${pid}/transcript`).then((t) => setSentences(t.sentences))
      .catch((e) => setErr(e?.message || 'No transcript yet'))
  }, [pid])

  const hits = useMemo(() => new Set(sentences ? search(sentences, q) : []), [sentences, q])
  const lo = a === null ? null : Math.min(a, b ?? a), hi = a === null ? null : Math.max(a, b ?? a)
  const pick = (i: number) => {
    if (a === null || b !== null) { setA(i); setB(null) } else setB(i)
  }
  const range = sentences && lo !== null && hi !== null ? { start: Math.floor(sentences[lo].start), end: Math.ceil(sentences[hi].end) } : null
  const add = () => {
    if (!range) return
    onAdd(`${fmt(range.start)}-${fmt(range.end)} ${kind}${note.trim() ? ` - ${note.trim()}` : ''}`)
    setA(null); setB(null); setNote('')
  }

  if (err) return <p className="text-xs text-muted">{err.includes('transcript') ? 'This sermon has no transcript yet. Start the reels once (or run transcription) and the transcript will appear here.' : err}</p>
  if (!sentences) return <p className="text-xs text-muted">Loading the transcript…</p>
  return (
    <div className="rounded-xl border border-line/70 p-2 space-y-2">
      <input className="input !py-1.5 text-sm" placeholder="Search the transcript…" value={q} onChange={(e) => setQ(e.target.value)} />
      <div ref={list} className="max-h-56 overflow-auto space-y-0.5 pr-1">
        {sentences.map((s, i) => {
          const on = lo !== null && hi !== null && i >= lo && i <= hi
          return (
            <button key={s.id} type="button" onClick={() => pick(i)}
              className={`w-full text-left rounded-md px-2 py-1 text-sm flex gap-2 ${on ? 'bg-primary/25 text-ink' : hits.has(s.id) ? 'bg-warn/15 text-ink' : 'hover:bg-white/5 text-muted'}`}>
              <span className="font-mono text-xs text-accent shrink-0 pt-0.5">{fmt(s.start)}</span><span>{s.text}</span>
            </button>
          )
        })}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {range ? <b className="text-ink">{fmt(range.start)} → {fmt(range.end)} ({range.end - range.start}s)</b>
          : <span className="text-muted">{a === null ? 'Click the sentence where the reel starts' : 'Now click the sentence where it ends'}</span>}
        <select className="input !w-auto !py-1" value={kind} onChange={(e) => setKind(e.target.value as 'main' | 'short')}>
          <option value="short">short</option><option value="main">main</option></select>
        <input className="input !py-1 flex-1 min-w-[8rem]" placeholder="What you want (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
        <button type="button" className="btn-primary !py-1" disabled={!range} onClick={add}>Add to reels</button>
      </div>
    </div>
  )
}
