// Pure logic of the Reel Editor (no React): choosing words, trimming by word, splitting, reordering, and the preview that
// jumps over the parts that are not in the reel. Kept apart so it can be tested with `npm test`.
export type Word = { id: string; w: string; start: number; end: number; confidence?: number | null; estimated?: boolean }
export type Sentence = { id: number; start: number; end: number; text: string; words: Word[] }
export type Seg = { start: number; end: number }

export const MIN_SEGMENT = 0.3
export const WARN_SECONDS = 60

export const total = (segs: Seg[]) => Math.round(segs.reduce((n, s) => n + Math.max(0, s.end - s.start), 0) * 1000) / 1000

/** The time range of the words i..j (either way round) of a list of words - what a click-and-drag selects. */
export function rangeFromWords(words: Word[], i: number, j: number): Seg {
  const a = Math.min(i, j), b = Math.max(i, j)
  return { start: words[a].start, end: words[b].end }
}

/** Add a range to the end of the reel. A range already inside a segment changes nothing; one that overlaps or touches the LAST
 *  segment grows it, so dragging across neighbouring sentences makes one segment. */
export function addRange(segs: Seg[], r: Seg): Seg[] {
  if (segs.some((s) => r.start >= s.start - 1e-6 && r.end <= s.end + 1e-6)) return segs
  const last = segs[segs.length - 1]
  if (last && r.start <= last.end + 0.35 && r.end >= last.start) return [...segs.slice(0, -1), { start: Math.min(last.start, r.start), end: Math.max(last.end, r.end) }]
  return [...segs, { start: r.start, end: r.end }]
}

export const remove = (segs: Seg[], i: number): Seg[] => segs.filter((_, k) => k !== i)

export function reorder(segs: Seg[], from: number, to: number): Seg[] {
  if (from === to || from < 0 || to < 0 || from >= segs.length || to >= segs.length) return segs
  const out = [...segs]
  const [m] = out.splice(from, 1)
  out.splice(to, 0, m)
  return out
}

/** Split a segment at a time inside it. Both halves must be at least MIN_SEGMENT long, else nothing changes. */
export function split(segs: Seg[], i: number, at: number): Seg[] {
  const s = segs[i]
  if (!s || at - s.start < MIN_SEGMENT || s.end - at < MIN_SEGMENT) return segs
  return [...segs.slice(0, i), { start: s.start, end: at }, { start: at, end: s.end }, ...segs.slice(i + 1)]
}

/** Move one edge of a segment by whole words: delta -1 = one word earlier, +1 = one word later (never past the other edge). */
export function trimByWord(segs: Seg[], i: number, edge: 'start' | 'end', delta: number, words: Word[]): Seg[] {
  const s = segs[i]
  if (!s) return segs
  const mid = (w: Word) => (w.start + w.end) / 2
  if (edge === 'start') {
    const idx = words.findIndex((w) => mid(w) >= s.start)
    const k = Math.max(0, Math.min(words.length - 1, (idx < 0 ? words.length - 1 : idx) + delta))
    const start = words[k].start
    return start >= s.end - MIN_SEGMENT ? segs : segs.map((x, n) => (n === i ? { ...x, start } : x))
  }
  let idx = -1
  words.forEach((w, n) => { if (mid(w) <= s.end) idx = n })
  const k = Math.max(0, Math.min(words.length - 1, (idx < 0 ? 0 : idx) + delta))
  const end = words[k].end
  return end <= s.start + MIN_SEGMENT ? segs : segs.map((x, n) => (n === i ? { ...x, end } : x))
}

/** Is this word inside the reel? (by its middle, the same rule the server uses) */
export const inReel = (w: Word, segs: Seg[]) => segs.some((s) => (w.start + w.end) / 2 >= s.start && (w.start + w.end) / 2 <= s.end)

/** Where is the reel at, for a given source time: which segment, and its time on the reel's own timeline. */
export function reelTime(segs: Seg[], idx: number, t: number): number {
  let before = 0
  for (let k = 0; k < idx; k++) before += segs[k].end - segs[k].start
  return before + Math.max(0, Math.min(t, segs[idx].end) - segs[idx].start)
}

/** The preview that jumps over removed parts without a render. Call on every timeupdate with the source video's current time:
 *  returns the segment now playing, a time to seek to (null = keep playing) and whether the reel is over. */
export function previewTick(segs: Seg[], idx: number, t: number): { idx: number; seek: number | null; done: boolean } {
  if (!segs.length) return { idx: 0, seek: null, done: true }
  const s = segs[idx]
  if (t < s.start - 0.05) return { idx, seek: s.start, done: false }          // before the segment (e.g. after a jump back)
  if (t >= s.end - 0.02) {
    if (idx + 1 >= segs.length) return { idx, seek: null, done: true }
    return { idx: idx + 1, seek: segs[idx + 1].start, done: false }           // jump over everything between the segments
  }
  return { idx, seek: null, done: false }
}

/** Search the transcript: the sentences whose text contains the phrase (case-insensitive). */
export const search = (sentences: Sentence[], phrase: string) => {
  const q = phrase.trim().toLowerCase()
  return q ? sentences.filter((s) => s.text.toLowerCase().includes(q)).map((s) => s.id) : []
}

export const fmt = (sec: number) => {
  const s = Math.max(0, Math.round(sec))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}` : `${m}:${String(r).padStart(2, '0')}`
}
