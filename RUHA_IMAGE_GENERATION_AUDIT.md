# RUHA — Image-Generation Audit

Inspection only. Nothing was changed, installed or regenerated to produce this report. Facts are tagged **[observed]** (read in code, DB, disk or on screen) or **[inferred]** (my reasoning from observed facts).

## 1. What RUHA's image engine actually is

There are **two separate image pipelines** in the app, plus a legacy Pillow path:

| # | Pipeline | Files | Produces | Uses AI image model? |
|---|---|---|---|---|
| A | **Themed posters / posts / reel cards** | `ruha/services/design.py`, `htmlrender.py`, `themes.py` | Sermon poster, encouragement posts, reel hook card, reel closing card | **No** (optional hook exists, off by default, nothing installed) |
| B | **Creative Studio ("Creative Engine 2.0")** | `ruha/creative/{brief,styles,formats,backends,compose,qc,studio,hardware}.py` | Instagram feed/square/reel-cover/hook, YouTube thumbnail, carousel, poster | Yes for 7 of 8 styles, **but no model is installed, so it cannot run on this PC** |
| C | Legacy Pillow renderer | `ruha/services/design.py` (`render_graphic` non-theme branch, `render_card` fallback) | Gradient poster, used only when the browser renderer is unavailable | No |
| D | Cutaway / image-reel stills | `ruha/services/cutaways.py`, `media.render_image_reel` | Animated scene stills inside reels | No (themed canvas; AI optional) |

Rendering technology **[observed]**:
- **HTML/CSS → headless Edge/Chrome screenshot** (`htmlrender._screenshot`: `msedge --headless=new --screenshot`). Edge and Chrome are both installed on this PC. This is the default engine (`design.engine = "html"`).
- **Pillow** drawing for the fallback engine and for all backgrounds (`themes.canvas`).
- **Stable Diffusion / SDXL / ComfyUI**: code exists (`creative/backends.py`, `ai/imagegen.py`), **nothing is installed or running** (see §6).
- No SVG pipeline, no external image API, no stock images. `privacy.allow_network` defaults to false.

## 2. Trace of one image: transcript → PNG

Traced: the poster `poster_v4.png` and reel hook card `main_1_open_v3.png` of the sermon project.

1. **Transcription** — `handlers.h_transcription` → `ai/whisper.FasterWhisperTranscriber` (model `base`, CPU, int8) → `transcript_segments` rows.
2. **Plan** — `handlers.ensure_plan` → `planner.py` builds the prompt (`PLAN_SCHEMA` + `_rules`), sends it to Ollama `qwen2.5:3b` (`ai/llm.OllamaLLM.generate_json`, `format=json`, `num_ctx=8192`). Long talks are chunked (`_map_prompt`/`_reduce_prompt`). Output goes through `normalize_plan`, which snaps clip times, validates scripture references against the transcript, and forces a theme with `themes.resolve`.
3. **Theme choice** — the LLM must pick **one of 9 words**: warfare, spirit, grace, peace, hope, faith, love, strength, growth (`planner.PLAN_SCHEMA`, `themes.THEMES`). Everything visual flows from this single word.
4. **Brand** — `brand.get_brand(app, project_id)`: name "El Ruach", tagline "The God's Breath", logo/mark, colours.
5. **Background** — `htmlrender._background_file` → `themes.canvas(size, theme, seed_text)`: a Pillow-drawn vertical gradient plus one motif drawn from ~7–70 random circles/polygons. For `warfare` the motif is `"embers"` (red glow ellipse + 70 random dots). The only per-image variation is the random seed derived from the headline text (`zlib.crc32`). An AI background replaces this only if `design.ai_background` is on **and** the model runs (it is off, and cannot run).
6. **Layout/typography** — `htmlrender.render_graphic` / `render_card`: builds one HTML page from `_BASE_CSS` with fixed structure: pill label → `<h1>` uppercase Montserrat 800 → ornament line + diamond → verse panel or reference → logo + name footer. A JS loop (`_FIT_JS`) shrinks the font until the text fits.
7. **Highlight** — `_key_words`: the **2 longest words > 4 letters, not in a stop list**, get the accent colour + glow (`.k`). This is why "BATTLE" and "FOLLOWERS" glow regardless of meaning.
8. **Export** — headless browser screenshot → Pillow crop/pad → `PNG optimize` → `<project>/posters/poster_v{n}.png` (cards in `cards/`, social in `social/`), registered by `services/assets.register` with a version row in `asset_versions`.

## 3. Source files that generate images **[observed]**

| File | Lines | Role |
|---|---|---|
| `ruha/services/themes.py` | 152 | 9 themes: 2 gradient colours + glow + accent + one of 7 motif types, Pillow-drawn |
| `ruha/services/htmlrender.py` | 278 | HTML/CSS templates, font loading, headless-browser screenshot |
| `ruha/services/design.py` | 395 | Pillow fallback renderer, `render_graphic`, `render_card`, font loader, `_fit`, `_wrap` |
| `ruha/services/cutaways.py` | 80 | Cutaway scene stills (themed canvas + keyword) |
| `ruha/creative/brief.py` | 214 | LLM creative brief → 3 metaphors + 3 headlines |
| `ruha/creative/styles.py` | 160 | 8 style definitions with prompt fragments, palettes, composition |
| `ruha/creative/formats.py` | 58 | 8 output formats with safe zones |
| `ruha/creative/compose.py` | 319 | Cover-crop, grade, scrim, transparent HTML text layer, composite |
| `ruha/creative/qc.py` | 130 | Pixel checks: clipping, safe margins, contrast, duplicate visual/text |
| `ruha/creative/backends.py` | 208 | `DiffusersBackend`, `ComfyBackend`, `ImageService` selector |
| `ruha/creative/hardware.py` | 62 | VRAM/RAM probe and model recommendation |
| `ruha/creative/studio.py` | 346 | Orchestration: brief → picture → compose → QC → asset |
| `ruha/ai/imagegen.py` | 67 | Older single-prompt SDXL-Turbo background generator (used by pipeline A) |
| `ruha/handlers.py` | 743 | `h_poster`, `h_social`, `_reel_cards`, `rerender_graphic`, `_cutaways` |

**Fonts [observed]** (`ruha/assets/fonts/`, all OFL): Montserrat (variable), Playfair Display (+italic), Cormorant Garamond (+italic), Bebas Neue, Poppins (3 weights, added for captions), Noto Sans for 10 Indic/Arabic scripts. Pipeline A uses Montserrat for every heading; Playfair Display for verses.

**Palette [observed]** (`themes.THEMES`): warfare `#100408→#45101a` glow `#ff3b30` accent `#ffb74d`; spirit `#170a2c→#4b1d6e`; grace `#2a1904→#80581a`; peace `#051c2a→#0f5f73`; hope `#1b1450→#d9703a`; faith `#040a1d→#17325f`; love `#2a0a1c→#8d1f4c`; strength `#0a1114→#2f4852`; growth `#06200f→#1f6e3d`. Brand colours in `DEFAULT_BRAND` (`#0B1020`, `#7C3AED`, `#22D3EE`…) are used only by the non-theme fallback.

**Prompt templates [observed]**: `ai/imagegen.THEME_SCENES` (9 one-line scene prompts + a shared `STYLE` and `NEGATIVE`), and `creative/styles.py` + `creative/brief.py` (`_prompt`) for Creative Studio. None of these has ever been sent to an image model on this machine **[observed]**: no `bg/` folder exists under any project and `ruha_data` has no generated-model images.

## 4. Why the designs look repetitive and basic — evidence

Observed on real files (poster, social post, hook card, creative poster; viewed directly).

1. **One layout for everything (`htmlrender.py:249-279`, `_BASE_CSS`).** Poster, both encouragement posts and every hook/closing card are the same HTML: bordered frame (`.frame`, `.frame2`) → pill → centred uppercase `h1` → ornament → footer. The poster and the social post I viewed are structurally identical; only the words differ.
2. **Nine backgrounds total (`themes.py:11-30`).** Every sermon about "battle" gets the same dark-red gradient with ~70 floating dots. The seed changes dot positions only. Three different assets in this project (poster, post, hook card) have visually the same background.
3. **No imagery at all.** Pipeline A never uses a picture — no subject, scene, texture, person, or symbol. The "visual storytelling" is a gradient plus bokeh dots. Nothing in the image relates to the sermon beyond its words.
4. **Single typeface, forced uppercase (`h1{text-transform:uppercase}`, `font-weight:800`).** All-caps heavy text for every message flattens hierarchy; long hooks become a 6-line wall of capitals (seen on `main_1_open_v3.png`: "IN THIS SERMON, WE'LL EXPLORE THE INTENSE SPIRITUAL BATTLE AGAINST SATAN AND HIS FOLLOWERS.").
5. **Keyword highlight is lexical, not semantic (`_key_words`).** It highlights the longest words ("FOLLOWERS", "BATTLE"), not the idea. The poster highlights "FOLLOWERS" but not "SATAN".
6. **Text comes straight from a 3B model.** `qwen2.5:3b` returns summary-like hooks; normalisation fixes grammar/length but not craft. Old v3 hook cards in this project still show the sentence form; the one-word hook rule is newer and only applies when re-rendered.
7. **Creative Studio has never produced AI artwork.** The 4 `creative` assets in the DB are all `style=minimal` (visual_source `typographic`). That style draws `procedural_background` (dark noise field) + text. The viewed `poster-minimal-1_v1.png` has text in the top third and an **empty bottom 55%** — a layout, not an illustration. The seven "photo" styles (cinematic, biblical, editorial, emotional, fineart, modern, movie) return "No image-generation model is available" on this PC (`backends.ImageService._pick`).
8. **Same logo block and frame on everything** (`.foot`, `.frame`), so brand consistency is high but variety is zero.
9. **Limited controls.** Settings expose only `design.engine`, `design.ai_background`, `design.ai_model`, `design.theme_backgrounds`; there is no per-asset layout choice in pipeline A (the `layout` argument only affects the non-theme legacy path).

**Verdict [inferred from the above]:** the output is clean and legible and the brand is consistent, but visually it is "template + gradient + text". The root cause is architectural: there is no image source other than 9 procedural gradients, and a single HTML template.

## 5. Image-quality findings on actual files

| File | Finding |
|---|---|
| `posters/poster_v4.png` | Clean, readable, no clipping. 5-line caps headline dominates; wide letter-spaced "HIGHLIGHT" pill is generic. Strong glow works. Looks like a template. |
| `social/social_1_v4.png` | Same frame/pill/footer as the poster; 3-line headline. Fine for Instagram legibility; zero visual differentiation from the poster. |
| `cards/main_1_open_v3.png` | 9 lines of caps on a 9:16 card; top and bottom crowd the frame; reads as a text wall. (Pre-dates the one-word-hook change.) |
| `creative/poster-minimal-1_v1.png` | Good Montserrat typography and QC-compliant margins, but a plain dark texture with a large empty area; no imagery. |
| `thumbs/*.jpg` | Frame grabs from the video; fine. |
| Text clipping | None observed in the files viewed; the fit loop and Creative QC prevented it. |
| Instagram suitability | Dimensions are right (1080×1350, 1080×1920); safe zones are only enforced in Creative Studio (`formats.py` `safe`), not in pipeline A. |

## 6. Hardware/model reality for image generation **[observed]**

- No NVIDIA GPU (`nvidia-smi` not found; only an AMD Radeon iGPU with 512 MB reported).
- Main venv has **no** `torch`, `diffusers`, `transformers`, `accelerate`.
- The translator venv has `torch 2.15.0+cpu`, CUDA not available, no diffusers.
- No Stable Diffusion/SDXL weights in the HF cache (cache holds Whisper tiny/base, IndicTrans2 en→indic, Indic-Parler-TTS, a 3 MB flan-t5 stub).
- ComfyUI is not running (nothing on port 8188).
- So **real AI image generation has never run on this machine**, and the code paths for it are **IMPLEMENTED BUT NOT VERIFIED**. The code is careful (VRAM gating, model unloading, QC, cache per concept) but is unproven end to end.

## 7. Main root causes of poor creative output, ranked

1. No image-generation model available (hardware + packages), so every design is procedural.
2. A single HTML template for all of pipeline A.
3. Only 9 procedural backgrounds, driven by one LLM-chosen word.
4. Small LLM (3B) writes hooks/headlines.
5. Lexical keyword highlighting.
6. Forced uppercase heavy type for every message.
7. Per-asset style/layout choice is not exposed in the main (non-Creative-Studio) flow.
