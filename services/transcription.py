from faster_whisper import WhisperModel

def transcribe(audio_path, model_size="small", device="auto", compute_type="int8"):
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        str(audio_path), beam_size=5, vad_filter=True,
        word_timestamps=False
    )
    output = []
    for seg in segments:
        output.append({
            "start": float(seg.start),
            "end": float(seg.end),
            "text": seg.text.strip()
        })
    # Release model before loading the LLM.
    del model
    return output, {
        "language": info.language,
        "language_probability": float(info.language_probability)
    }

def save_transcript(segments, path):
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)
    with open(path.with_suffix(".txt"), "w", encoding="utf-8") as f:
        for s in segments:
            f.write(f"[{s['start']:.2f} - {s['end']:.2f}] {s['text']}\n")
