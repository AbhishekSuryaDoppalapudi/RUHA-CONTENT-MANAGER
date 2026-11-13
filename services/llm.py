import os, requests, json, re

def ask_ollama(prompt, model=None, base_url=None):
    model = model or os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
    try:
        r = requests.post(
            f"{base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False,
                  "format": "json", "options": {"temperature": 0.35}},
            timeout=900
        )
        r.raise_for_status()
        return r.json()["response"]
    except requests.RequestException as e:
        raise RuntimeError(
            f"Could not reach Ollama at {base_url}. Start Ollama and pull model {model}. Details: {e}"
        )

def extract_json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError("The local model did not return valid JSON.")
        return json.loads(match.group(0))

def analyse_transcript(transcript, duration, model=None):
    # Limit prompt size for 16 GB systems. Preserve transcript in DB/files separately.
    compact = transcript[:45000]
    prompt = f'''
You are a careful sermon and long-form video content editor.
Analyse the transcript and return ONLY valid JSON with this schema:
{{
 "title":"short descriptive title",
 "main_theme":"one sentence",
 "summary":"faithful summary",
 "key_points":["..."],
 "main_reels":[{{"start":0,"end":45,"hook":"...","reason":"...","caption":"..."}}],
 "short_reels":[{{"start":0,"end":20,"hook":"...","reason":"...","caption":"..."}}],
 "poster":{{"headline":"...","subheadline":"...","quote":"..."}},
 "encouragement_posts":[{{"headline":"...","body":"...","caption":"...","scripture_reference":""}}],
 "hashtags":["..."]
}}
Requirements:
- Produce exactly 3 main_reels and 2 short_reels.
- Main reels should generally be 35-90 seconds; short reels 15-30 seconds.
- Choose timestamps only within 0 and {duration:.1f} seconds.
- Prefer complete thoughts, strong stories, practical teaching and emotional clarity.
- Never invent quotes or Bible references. If no reference is explicitly supported, use an empty string.
- Do not distort the speaker's meaning. Do not create a clip from a fragment that needs missing context.
- Encouragement posts must be genuinely distinct and faithful to the source.
- Use the transcript timestamps to select the segments.
TRANSCRIPT:
{compact}
'''
    raw = ask_ollama(prompt, model=model)
    data = extract_json(raw)
    return validate_analysis(data, duration)

def validate_analysis(data, duration):
    for key, count in [("main_reels", 3), ("short_reels", 2)]:
        items = data.get(key, [])
        if len(items) != count:
            raise ValueError(f"AI returned {len(items)} {key}; expected {count}. Re-run analysis.")
        for item in items:
            start = max(0.0, float(item.get("start", 0)))
            end = min(float(duration), float(item.get("end", start + 1)))
            if end <= start:
                raise ValueError(f"Invalid clip timestamps in {key}.")
            item["start"], item["end"] = start, end
    posts = data.get("encouragement_posts", [])
    if len(posts) != 2:
        raise ValueError("AI must return exactly two encouragement posts.")
    data.setdefault("poster", {})
    data.setdefault("hashtags", [])
    data.setdefault("key_points", [])
    return data
