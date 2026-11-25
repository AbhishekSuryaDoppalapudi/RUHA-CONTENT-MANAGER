import { test } from 'node:test'
import assert from 'node:assert/strict'
import { addRange, inReel, previewTick, rangeFromWords, remove, reorder, reelTime, search, split, total, trimByWord } from '../src/reelLogic.ts'

const words = [0, 1, 2, 3, 4, 5].map((i) => ({ id: `0:${i}`, w: `w${i}`, start: i * 2, end: i * 2 + 1 }))

test('dragging across words selects from the first start to the last end, either direction', () => {
  assert.deepEqual(rangeFromWords(words, 1, 3), { start: 2, end: 7 })
  assert.deepEqual(rangeFromWords(words, 3, 1), { start: 2, end: 7 })
})

test('adding ranges: new ones append, repeats are ignored, touching the last one grows it', () => {
  let s = addRange([], { start: 2, end: 7 })
  s = addRange(s, { start: 20, end: 25 })
  assert.equal(s.length, 2)
  assert.equal(addRange(s, { start: 3, end: 5 }), s)
  assert.deepEqual(addRange(s, { start: 25.1, end: 30 }).at(-1), { start: 20, end: 30 })
})

test('deleting a segment updates the total length', () => {
  const s = [{ start: 0, end: 10 }, { start: 50, end: 70 }, { start: 90, end: 95 }]
  assert.equal(total(s), 35)
  assert.equal(total(remove(s, 1)), 15)
})

test('reordering moves a segment and keeps the others in order', () => {
  const s = [{ start: 0, end: 1 }, { start: 10, end: 11 }, { start: 20, end: 21 }]
  assert.deepEqual(reorder(s, 2, 0).map((x) => x.start), [20, 0, 10])
  assert.equal(reorder(s, 1, 1), s)
})

test('splitting makes two segments whose total is unchanged; a split too near an edge does nothing', () => {
  const s = [{ start: 0, end: 10 }]
  const out = split(s, 0, 4)
  assert.deepEqual(out, [{ start: 0, end: 4 }, { start: 4, end: 10 }])
  assert.equal(total(out), total(s))
  assert.equal(split(s, 0, 0.1), s)
})

test('trimming by word moves an edge one word at a time and never past the other edge', () => {
  const s = [{ start: 2, end: 7 }]                       // words w1..w3
  assert.deepEqual(trimByWord(s, 0, 'start', 1, words)[0], { start: 4, end: 7 })
  assert.deepEqual(trimByWord(s, 0, 'start', -1, words)[0], { start: 0, end: 7 })
  assert.deepEqual(trimByWord(s, 0, 'end', 1, words)[0], { start: 2, end: 9 })
  assert.deepEqual(trimByWord(s, 0, 'end', -1, words)[0], { start: 2, end: 5 })
  const one = [{ start: 2, end: 3 }]
  assert.equal(trimByWord(one, 0, 'end', -1, words), one)  // would leave nothing
})

test('words inside the reel are highlighted', () => {
  const s = [{ start: 2, end: 7 }]
  assert.deepEqual(words.map((w) => inReel(w, s)), [false, true, true, true, false, false])
})

test('the preview jumps over removed parts and ends after the last segment', () => {
  const s = [{ start: 10, end: 20 }, { start: 100, end: 110 }]
  assert.deepEqual(previewTick(s, 0, 12), { idx: 0, seek: null, done: false })
  assert.deepEqual(previewTick(s, 0, 20), { idx: 1, seek: 100, done: false })      // jumps from 20 s straight to 100 s
  assert.deepEqual(previewTick(s, 1, 105), { idx: 1, seek: null, done: false })
  assert.deepEqual(previewTick(s, 1, 110), { idx: 1, seek: null, done: true })
  assert.equal(previewTick(s, 1, 5).seek, 100)                                      // seeked back: return into the segment
  assert.equal(reelTime(s, 1, 105), 15)                                             // 10 s of the first segment + 5 s
})

test('search finds sentences containing the phrase, ignoring case', () => {
  const sents = [{ id: 0, start: 0, end: 1, text: 'Grace is free', words: [] }, { id: 1, start: 1, end: 2, text: 'Hello there', words: [] }]
  assert.deepEqual(search(sents, ' GRACE '), [0])
  assert.deepEqual(search(sents, ''), [])
})
