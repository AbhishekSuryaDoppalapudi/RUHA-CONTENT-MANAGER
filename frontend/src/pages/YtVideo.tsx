import { useEffect, useState } from 'react'
import { Clapperboard, Download, ListChecks, Mic, Play, Sparkles, Video } from 'lucide-react'
import { api } from '../api'
import { Empty, ErrorBox, Field, Loading, PageHeader, ProgressBar, Spinner, StatusBadge, useApi, useToast } from '../ui'

type Scene = { id: number; text: string; visual: string }
type YtJob = { id: number; job_type: string; status: string; progress: number; step: string; error: string | null }
type YtState = {
  name: string; scenes: Scene[]; narration: Record<string, number>; clips: number
  videos: { lang: string; file: string; srt: boolean }[]; jobs?: YtJob[]
}
type Options = { languages: string[]; sizes: string[]; default_style: string }

const SAMPLE = `Visual: a quiet village at sunrise, golden light

Long ago, in a quiet village, there lived a child named Ravi. Every morning he walked to the river to fetch water.

Visual: a boy meets an old man by the river

One day an old man told him, a small kindness done every day can change the whole world.`

export default function YtVideo() {
  const toast = useToast()
  const opts = useApi(() => api.get<Options>('/api/yt/options'), [])
  const list = useApi(() => api.get<{ items: { name: string; scenes: number }[] }>('/api/yt/projects'), [])
  const [name, setName] = useState('mystory')
  const [script, setScript] = useState('')
  const [style, setStyle] = useState('')
  const [words, setWords] = useState(22)
  const [lang, setLang] = useState('English')
  const [size, setSize] = useState('1920x1080')
  const [burn, setBurn] = useState(false)
  const [busy, setBusy] = useState('')
  const st = useApi(() => (name ? api.get<YtState>(`/api/yt/projects/${encodeURIComponent(name)}`) : Promise.resolve(null)), [name], 2500)
  const data = st.data
  const jobs = data?.jobs ?? []
  const running = jobs.find((j) => ['queued', 'running'].includes(j.status))
  const lastFail = jobs[0]?.status === 'failed' ? jobs[0] : undefined
  const done = !!data?.narration[lang]
  const video = data?.videos.find((v) => v.lang === lang)
  useEffect(() => { if (!running) st.reload() }, [running?.id]) // eslint-disable-line

  const act = async (key: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(key)
    try { await fn(); toast.ok(ok); st.reload(); list.reload() } catch (e) { toast.err(e) } finally { setBusy('') }
  }
  const base = `/api/yt/projects/${encodeURIComponent(data?.name || name)}`

  return (
    <>
      <PageHeader title="YT Video Making" subtitle="Turn a script into a finished video: plan scenes, make the narration, add clips, and build the video. Everything runs on this computer." />

      <div className="card p-4 mb-5 text-sm flex gap-3 items-start">
        <Clapperboard size={20} className="text-teal mt-0.5" />
        <p className="text-muted"><b className="text-ink">Clips are plain colour test clips for now.</b> Real animated clips need a GPU machine; add them as 001.mp4, 002.mp4 &hellip; in the project folder and the same steps will use them. The narration always uses the male voice.</p>
      </div>

      <section className="card p-5 mb-5">
        <h2 className="font-display text-lg mb-1 flex items-center gap-2"><ListChecks size={18} /> 1. Script and scenes</h2>
        <p className="text-xs text-muted mb-3">Separate paragraphs with a blank line. Start a paragraph with a line &ldquo;Visual: &hellip;&rdquo; to describe what should be shown.</p>
        <Field label="Your script">
          <textarea className="input min-h-[10rem]" value={script} onChange={(e) => setScript(e.target.value)} placeholder="Paste or type your story here…" />
        </Field>
        <div className="mt-2"><button className="btn-ghost !py-1 text-xs" onClick={() => setScript(SAMPLE)}>Insert a sample script</button></div>
        <div className="grid sm:grid-cols-3 gap-3 mt-3">
          <Field label="Project name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Visual style (added to every scene)"><input className="input" value={style} placeholder={opts.data?.default_style} onChange={(e) => setStyle(e.target.value)} /></Field>
          <Field label={`Max words per scene: ${words}`}><input type="range" min={10} max={40} value={words} onChange={(e) => setWords(Number(e.target.value))} className="w-full" /></Field>
        </div>
        <div className="flex flex-wrap gap-2 mt-4 items-center">
          <button className="btn-primary" disabled={busy === 'plan' || !script.trim()}
            onClick={() => act('plan', () => api.post('/api/yt/plan', { script, project: name, style: style || null, max_words: words }), 'Scenes planned')}>
            {busy === 'plan' ? <Spinner /> : <Sparkles size={16} />} Plan scenes</button>
          {(list.data?.items.length ?? 0) > 0 && (
            <select className="input !w-auto" value="" onChange={(e) => e.target.value && setName(e.target.value)} aria-label="Open an earlier project">
              <option value="">Open an earlier project…</option>
              {list.data!.items.map((p) => <option key={p.name} value={p.name}>{p.name} ({p.scenes} scenes)</option>)}
            </select>)}
        </div>
        {data && data.scenes.length > 0 && (
          <div className="mt-4 overflow-x-auto">
            <p className="text-sm mb-2"><b>{data.scenes.length} scenes</b> · about {(data.scenes.reduce((n, s) => n + s.text.split(/\s+/).length, 0) / 150).toFixed(1)} min of narration</p>
            <table className="w-full text-sm"><thead><tr className="text-left text-xs text-muted"><th className="py-1 pr-3 w-10">#</th><th className="pr-3">Narration</th><th>Visual</th></tr></thead>
              <tbody>{data.scenes.map((s) => <tr key={s.id} className="border-t border-line/50 align-top"><td className="py-1.5 pr-3">{s.id}</td><td className="pr-3">{s.text}</td><td className="text-muted">{s.visual}</td></tr>)}</tbody></table>
          </div>)}
      </section>

      <section className="card p-5 mb-5">
        <h2 className="font-display text-lg mb-1 flex items-center gap-2"><Mic size={18} /> 2. Narration</h2>
        <p className="text-xs text-muted mb-3">The voice is generated on this computer. It is slow without a GPU (about a minute per scene).</p>
        <div className="grid sm:grid-cols-3 gap-3 items-end">
          <Field label="Narration language">
            <select className="input" value={lang} onChange={(e) => setLang(e.target.value)}>{(opts.data?.languages ?? ['English']).map((l) => <option key={l}>{l}</option>)}</select></Field>
          <div><button className="btn-primary" disabled={!data?.scenes.length || !!running || busy === 'nar'}
            onClick={() => act('nar', () => api.post(`${base}/narrate`, { lang }), 'Narration queued - see progress below')}>
            {busy === 'nar' ? <Spinner /> : <Mic size={16} />} Make narration</button></div>
          <p className="text-sm text-muted">{done ? <>Ready in {lang}: {data!.narration[lang]} min</> : 'Not made yet'}</p>
        </div>
      </section>

      <section className="card p-5 mb-5">
        <h2 className="font-display text-lg mb-1 flex items-center gap-2"><Video size={18} /> 3. Build the video</h2>
        <p className="text-xs text-muted mb-3">Clips found for this project: {data?.clips ?? 0}. Missing clips are filled with test clips.</p>
        <div className="grid sm:grid-cols-3 gap-3 items-end">
          <Field label="Video size"><select className="input" value={size} onChange={(e) => setSize(e.target.value)}>{(opts.data?.sizes ?? ['1920x1080']).map((s) => <option key={s}>{s}</option>)}</select></Field>
          <label className="flex items-center gap-2 text-sm pb-2"><input type="checkbox" checked={burn} onChange={(e) => setBurn(e.target.checked)} /> Burn subtitles into the picture</label>
          <div><button className="btn-primary" disabled={!done || !!running || busy === 'build'}
            onClick={() => act('build', () => api.post(`${base}/build`, { lang, size, burn }), 'Building the video - see progress below')}>
            {busy === 'build' ? <Spinner /> : <Play size={16} />} Build video</button></div>
        </div>
        <div className="mt-3"><button className="btn-ghost" disabled={!data?.scenes.length || !!running || busy === 'all'}
          onClick={() => act('all', () => api.post(`${base}/run-all`, { lang, size, burn }), 'Narration and video queued')}>
          Run all steps</button></div>
      </section>

      {running && (
        <div className="card p-4 mb-5">
          <div className="flex items-center gap-2 text-sm mb-2"><Spinner /> <b>{running.job_type === 'yt_narrate' ? 'Making narration' : 'Building video'}</b>
            <StatusBadge status={running.status} /><span className="text-muted">{running.step}</span></div>
          <ProgressBar value={running.progress} />
        </div>)}
      {lastFail && !running && <div className="mb-5"><ErrorBox error={new Error(lastFail.error || 'The last step failed')} /></div>}

      <section className="card p-5">
        <h2 className="font-display text-lg mb-3">Finished video</h2>
        {st.loading && !data ? <Loading rows={1} /> : video ? (
          <div>
            <video key={`${data!.name}-${lang}-${running ? 0 : 1}`} className="w-full max-w-3xl rounded-xl bg-black" controls src={`${base}/video?lang=${encodeURIComponent(lang)}`} />
            <div className="flex gap-2 mt-3">
              <a className="btn-ghost" href={`${base}/video?lang=${encodeURIComponent(lang)}&download=true`}><Download size={16} /> Download video</a>
              {video.srt && <a className="btn-ghost" href={`${base}/subtitles?lang=${encodeURIComponent(lang)}`}><Download size={16} /> Subtitles (.srt)</a>}
            </div>
          </div>
        ) : <Empty icon={<Video size={22} />} title="No video yet" hint={`Plan the scenes, make the ${lang} narration, then build the video.`} />}
      </section>
    </>
  )
}
