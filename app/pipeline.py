from pathlib import Path
import json, uuid, shutil, os
from datetime import datetime
from database.db import init_db, create_project, update_status, add_asset
from services.video import probe_video, extract_audio, render_clip, write_srt
from services.transcription import transcribe, save_transcript
from services.llm import analyse_transcript
from services.design import make_graphic

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT / "storage" / "projects"
TEMP = ROOT / "storage" / "temp"

def run_pipeline(video_path, ollama_model=None, whisper_model="small",
                 whisper_device="auto", compute_type="int8", progress=None):
    init_db()
    video_path = Path(video_path).resolve()
    if not video_path.exists():
        raise FileNotFoundError(video_path)
    info = probe_video(video_path)
    project_id = uuid.uuid4().hex[:12]
    title = video_path.stem
    out = PROJECTS / project_id
    for folder in ["transcript", "analysis", "reels", "posters", "social_posts", "audio"]:
        (out / folder).mkdir(parents=True, exist_ok=True)
    create_project(project_id, title, str(video_path), str(out))
    def report(pct, msg):
        if progress:
            progress(pct, msg)
    try:
        update_status(project_id, "processing")
        report(5, "Extracting audio")
        audio = out / "audio" / "source.wav"
        extract_audio(video_path, audio)

        report(18, "Transcribing video locally")
        segments, language = transcribe(audio, whisper_model, whisper_device, compute_type)
        transcript_json = out / "transcript" / "transcript.json"
        save_transcript(segments, transcript_json)
        add_asset(project_id, "transcript_json", transcript_json, language)
        add_asset(project_id, "transcript_txt", transcript_json.with_suffix(".txt"), language)

        report(40, "Analysing transcript with local Ollama model")
        full_text = "\n".join(
            f"[{s['start']:.2f}-{s['end']:.2f}] {s['text']}" for s in segments
        )
        analysis = analyse_transcript(full_text, info["duration"], model=ollama_model)
        analysis_path = out / "analysis" / "content_plan.json"
        analysis_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "analysis" / "content_plan.md").write_text(
            "# " + analysis.get("title", title) + "\n\n" +
            "## Main theme\n" + analysis.get("main_theme", "") + "\n\n" +
            "## Summary\n" + analysis.get("summary", "") + "\n\n" +
            "## Key points\n" + "\n".join("- " + x for x in analysis.get("key_points", [])),
            encoding="utf-8"
        )
        add_asset(project_id, "analysis", analysis_path)

        report(52, "Rendering five vertical reels")
        for group, prefix in [("main_reels", "main"), ("short_reels", "short")]:
            for idx, clip in enumerate(analysis[group], 1):
                stem = f"{prefix}_{idx}"
                mp4 = out / "reels" / f"{stem}.mp4"
                srt = out / "reels" / f"{stem}.srt"
                render_clip(video_path, clip["start"], clip["end"], mp4)
                write_srt(segments, clip["start"], clip["end"], srt)
                meta = {**clip, "subtitle_file": str(srt)}
                (out / "reels" / f"{stem}.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                add_asset(project_id, "reel", mp4, meta)
                add_asset(project_id, "subtitle", srt, meta)

        report(82, "Creating poster and encouragement graphics")
        poster = analysis.get("poster", {})
        poster_path = out / "posters" / "highlight_poster.png"
        make_graphic(poster.get("headline", analysis.get("title", title)),
                     poster.get("quote", poster.get("subheadline", "")),
                     poster_path, "SERMON HIGHLIGHT")
        add_asset(project_id, "poster", poster_path)

        captions = ["# Social content\n"]
        for i, post in enumerate(analysis["encouragement_posts"], 1):
            graphic = out / "social_posts" / f"encouragement_{i}.png"
            body = post.get("body", "")
            if post.get("scripture_reference"):
                body += "\n\n" + post["scripture_reference"]
            make_graphic(post.get("headline", f"Encouragement {i}"), body, graphic, "ENCOURAGEMENT")
            add_asset(project_id, "social_post_image", graphic)
            captions.append(f"## Encouragement post {i}\n**{post.get('headline','')}**\n\n{body}\n\n{post.get('caption','')}\n")
        captions.append("## Reel captions and hashtags\n")
        for group in ["main_reels", "short_reels"]:
            for i, clip in enumerate(analysis[group], 1):
                captions.append(f"### {group} {i}\n{clip.get('hook','')}\n\n{clip.get('caption','')}\n")
        captions.append(" ".join(analysis.get("hashtags", [])))
        captions_path = out / "social_posts" / "captions_and_posts.md"
        captions_path.write_text("\n".join(captions), encoding="utf-8")
        add_asset(project_id, "captions", captions_path)

        report(100, "Completed")
        update_status(project_id, "completed")
        return {"project_id": project_id, "title": title, "output_dir": str(out),
                "analysis": analysis, "language": language}
    except Exception as e:
        update_status(project_id, "failed", str(e))
        raise
