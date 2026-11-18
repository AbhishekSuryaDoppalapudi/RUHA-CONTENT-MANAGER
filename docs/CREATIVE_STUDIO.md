# Creative Studio (Creative Engine 2.0)

## 1. Audit of the previous engine (what was found)

| Question | Finding |
|---|---|
| How were images made? | Entirely in code. `services/themes.py` drew nine Pillow "motif" canvases (embers, rays, waves, bokeh…); `services/htmlrender.py` / `design.py` overlaid text. **No AI image model was involved.** An optional SDXL-Turbo hook (`ai/imagegen.py`) existed but was off by default and only produced a generic scene per theme. |
| Why the same look every time? | Nine themes × one layout. The picture never depended on the sermon beyond the theme name; the AI only wrote *text* (hooks). Everything was forced uppercase with a glowing keyword; one centred composition for every format (formats were resized, not designed). |
| Prompts | The planner prompts only asked for headlines, verses and a theme word. Nothing described imagery, composition or light. |
| Storage / preview | Assets are `GeneratedAsset` rows (kinds poster, social_post, reel cards, image_reel) with files in the project workspace; previews are served by `/api/assets/{id}/preview`; review happens in Review / AssetDetail. |
| Categories | Poster, encouragement posts, reel hook/closing cards, image reels. No covers, thumbnails, carousels or concept variants. |
| Local models available | Ollama `qwen2.5:3b` + `nomic-embed-text`. No image model, no torch/diffusers, no ComfyUI. |
| Dev laptop | AMD integrated graphics, 15 GB RAM (about 3 GB free), no NVIDIA GPU. **Real image generation cannot run on it.** Target machine: RTX 3060, 16 GB RAM. |

Video translation, dubbing, transcription, reels and project management are untouched. The legacy poster/post pipeline still runs and is labelled as a template design in its metadata.

## 2. Architecture

```
transcript ─► creative brief (LLM) ─► concepts (code) ─► STAGE 1 picture (image model, text-free)
                                                    └─► STAGE 2 design (browser typography on a transparent layer)
                                                              └─► QC on real pixels ─► asset (draft / revision_requested)
```

* `ruha/creative/brief.py` – the brief: message, emotional theme, audience, three visual metaphors, three headlines, supporting line,
  scripture reference. The model supplies meaning; code enforces trust: scripture comes only from references the speaker said
  (planner guards), unspoken references/quotes are stripped, sacred-figure or demonic subjects are rejected, near-duplicate
  metaphors/headlines are replaced, headlines are length-limited per format. If the model fails, the brief is built from the stored plan
  and says so (`source`).
* `ruha/creative/styles.py` – **eight engines**, each with its own prompt language, colour grade, typography system and text-zone logic:
  Cinematic photography · Biblical cinematic · Premium editorial · Emotional storytelling · Fine art · Minimalist typography ·
  Modern Christian media · YouTube movie-poster.
* `ruha/creative/formats.py` – reel cover 1080×1920, feed 1080×1350, square 1080×1080, YouTube thumbnail 1280×720, carousel slides, highlight
  poster, hook image (4:5 and 9:16). Each has its own safe area and per-aspect text zone (thumbnails are left-weighted, reel covers keep
  clear of the Reels interface). A picture is generated per aspect ratio, not resized.
* `ruha/creative/backends.py` – image back-ends: local Stable Diffusion (diffusers) or a running ComfyUI. Models load only for a batch,
  the LLM is unloaded first, and the image model is unloaded afterwards (one heavy model at a time). A GPU/RAM gate refuses to load when
  memory is busy.
* `ruha/creative/compose.py` – grades the picture, adds a text scrim, then draws **all words** in a browser onto a *transparent* layer
  (bundled open fonts, correct Indian-language shaping, automatic font fitting, balanced line breaks). Mixed case by default; uppercase
  only in the movie-poster engine and for small labels.
* `ruha/creative/qc.py` – checks every creative (below). Nothing is auto-approved.
* `ruha/creative/studio.py` – the job workflow, targeted automatic repairs (shorten headline, shrink, stronger scrim, new seed), history via
  asset versions, headline recompose (no new picture), regenerate, benchmark.

## 3. Quality checks (each creative)

aspect ratio · text fits its box · mobile readability (minimum headline size) · hierarchy · safe margins · no clipping · contrast of headline,
supporting text and reference **against the picture beneath them** (WCAG 3:1 / 4.5:1) · image not flat / sharpness · duplicate picture
(perceptual hash) · duplicate headline · scripture integrity · no invented references in the text · headline length · brand lockup.

`visual_relevance` is always reported as **manual**: no vision model is installed, so whether the picture fits the message needs a person.
A creative that passes is `draft`; one that fails after the automatic repairs is `revision_requested`. Approval is always a human action.

## 4. Choosing a model for an RTX 3060

`creative/hardware.py` reads VRAM from `nvidia-smi` and recommends:

| VRAM | Model | Why |
|---|---|---|
| ≥ 10.5 GB (desktop RTX 3060 12 GB) | SDXL 1.0, fp16, 832×1216 | Fits fully on the GPU; best detail. OpenRAIL++ licence (commercial use allowed). |
| 7.5 – 10 GB | SD 1.5, fp16, 512×768 | Fast and comfortable. SDXL would need CPU offload and run several times slower. |
| 3.5 – 7.5 GB (laptop RTX 3060 6 GB) | SD 1.5 + attention slicing + CPU offload | Fits; slower. |
| < 3.5 GB / none | not recommended | CPU generation takes many minutes per picture; off unless enabled in Settings. |

SDXL-Turbo is selectable (very fast) but its licence is non-commercial. ComfyUI is supported as an alternative back-end (set the address in
Settings → Creative Studio). **No benchmark has been run on the target 3060 yet** - press *Run benchmark* in the Creative Studio there; it records
seconds for the first picture and peak VRAM and shows them on the page.

## 5. What is verified and what is not

Verified by automated tests (fake image service, real browser typography): hardware recommendation, brief guards, eight distinct engines,
three distinct concepts, per-format sizes/zones, text never clipped and inside safe areas, contrast/overflow/duplicate/scripture checks, the full
API workflow (three poster concepts, reel covers, thumbnails, carousel, encouragement posts, recompose, regenerate, history, approve/reject),
ComfyUI protocol against a local fake server, honest "no model" behaviour.

**Not verified on this laptop:** actual Stable Diffusion image quality, speed and VRAM use, and the ComfyUI path against a real server - this
machine has no NVIDIA GPU. Test pictures in the suite are synthetic placeholders, never presented as AI artwork.
