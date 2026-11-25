# RUHA — CPU-Only Creative Engine Test Report

Date: 2026-11-16. Testing and diagnosis only; nothing was redesigned. All test code and outputs are in `cpu_creative_test/` (isolated data folder `cpu_creative_test/data`, outputs `cpu_creative_test/out`). The production database and projects were never opened.

**Labels used below:** **ENGINE** = an existing RUHA feature, run as shipped. **EXPERIMENT** = something I added for the test, not part of RUHA. **SYNTHETIC PLATE** = a procedurally generated dark texture from the engine's own `procedural_background()`, used only so the typography of photo-style treatments could be rendered. It is not AI art and not a photograph.

## 1. Environment
| Item | Value |
|---|---|
| OS | Windows 11 Home 10.0.26300 |
| CPU | AMD Ryzen 5 5500U, 6 cores / 12 threads |
| RAM | 15.3 GB total; **only 2.2–3.8 GB free** during the test (browser and other apps hold the rest) |
| GPU | AMD Radeon integrated graphics (about 0.5 GB reported). **No NVIDIA GPU, no CUDA** (`nvidia-smi` not found) |
| PyTorch / diffusers / transformers | **Not installed** |
| Other libraries present | onnxruntime, Pillow, numpy, chromadb, faster-whisper, psutil |
| Ollama | Installed and running; models `qwen2.5:3b` (1.9 GB), `nomic-embed-text` (0.27 GB). No image model |
| ComfyUI | Not reachable at 127.0.0.1:8188 |
| Disk | 584 GB free on C:. The test created about 33 MB |

## 2. Existing engine architecture (inspected before testing)
- `ruha/creative/brief.py`: LLM (Ollama) writes a brief (message, theme, 3 visual metaphors, 3 headlines); scripture references are filtered in code; `expand_concepts` makes one concept per style.
- `styles.py`: 8 treatments (cinematic, biblical, editorial, emotional, fineart, minimal, modern, movie). Seven are `photo` (need an image model); `minimal` is typographic.
- `formats.py`: reel cover 1080×1920, feed 1080×1350, square 1080×1080, thumbnail 1280×720, carousel, poster, hook, each with its own safe area and word limit.
- `backends.py` / `hardware.py`: diffusers (needs torch + NVIDIA GPU) or ComfyUI. If neither exists it raises a plain-language error.
- `compose.py`: typography as HTML/CSS on a transparent layer rendered by headless Edge, composited over the picture. `qc.py`: pixel-level checks.
- Legacy path (`design.py`, `themes.py`): HTML/CSS cards over Pillow-drawn procedural backgrounds. `ai/imagegen.py` is the older GPU-only background generator, off by default.
- **There is no curated photo library or stock-image workflow.** `ruha/assets` holds only fonts and the logo. So the "select different photographs per concept" fallback does not exist and could not be tested.

## 3. Test A: creative intelligence
**Planner (ENGINE, real LLM): failed.** On the 171 s test sermon, `create_plan` was rejected twice with "A main_reels entry is too short after snapping to the transcript." Two attempts took 350 s. The sermon was far shorter than a real one (the plan asks for a 35–90 s main reel), but this is the same family of failure as production job #37 ("0 main_reels") and is evidence for that investigation. During this run free RAM fell to 0.08 GB; my safety guard asked Ollama to unload and nothing crashed.

**Creative brief (ENGINE, real LLM, fed a tester-written plan stub): worked in 61 s.**
- Headlines: "What's the Thorn in Your Flesh?", "Your Weakness is the Key to His Strength", "Bring Your Weakness to the Altar". All three come from the sermon and read as ideas, not single words.
- Visual metaphors: only the first is a good picture ("cracked clay pot, light shining through the cracks"). The other two ("closed bag / door", "clenched fist / open hands") are lifted from the sermon text and are weak or unphotographable. The code's safety filter did not reject them.

**EXPERIMENT (my own prompt, 31 s):** 5 idea-hooks, 1 poster headline, 2 post concepts. Examples: "Embrace your weakness, not hide it." (grammatically broken), "Surrendering isn't giving up, it's trusting." (strong), "When your strength fades, His shines brighter." (strong), "Pray with honesty, not just for show." (plain). Two of the five are good, two are acceptable, one is faulty. The model returned six hooks when asked for five.

**Important finding (ENGINE):** the existing one-word-hook rule (`planner.one_word_hook`, applied inside `normalize_plan`) reduced the idea-hooks to generic words:
| Idea hook | Becomes |
|---|---|
| Embrace your weakness, not hide it. | Strength |
| Surrendering isn't giving up, it's trusting. | Faith |
| God's strength is found in our weaknesses. | Strength |
| Pray with honesty, not just for show. | Pray |
| When your strength fades, His shines brighter. | Strength |
| Weakness is the gateway to His power. | Strength |
| Your Weakness Is Not the End of Your Story | Strength |
| When Your Strength Ends, His Begins | Strength |

Six of eight hooks become "Strength", and the word comes from a keyword table, not from the sermon. This directly conflicts with the new requirement (idea-communicating hooks, avoid BATTLE/FAITH/STRENGTH). Earlier you asked for one-word hooks, so this is a product decision for you, not a bug. In this test the plan's reel hooks were therefore generic; the better hooks came only from the creative-brief path.

**Counts requested vs. what the engine natively produces:** 5 hooks (engine: 3 brief headlines + reel hooks that become single words), 3 alternative visual concepts (engine: 3 metaphors, two weak), 1 poster concept (yes), 2 encouragement posts (yes, in the plan), 3 main reel covers and 2 short covers (yes via `expand_concepts`, I assembled them).

## 4. Test B: visual concept definition
Each concept carries: subject, environment, visual metaphor, lighting, palette, composition (a "copy-space" phrase), text zone and aspect ratio. **Missing: camera perspective and an explicit mood field** (only a free-text `creative_direction` for the whole brief). Negative space is implied by the zone and the copy-space phrase, not stored as its own field. Lighting and palette are picked from fixed per-style/per-theme lists, so they vary by style but are not derived from the sermon. Five reel concepts used three different subjects, but because no picture exists, the subjects never reached the final image.

## 5. Test C: image generation and fallback
- `images.status()` returned `available: false` with an accurate reason (ComfyUI unreachable; diffusers not installed). `generate()` raised `ImageBackendError`. **Unavailable AI is identified correctly, not disguised.** (ENGINE, passed.)
- Photographic styles cannot produce art on this laptop. In the studio workflow, `h_generation` refuses them with a message that only "Minimalist typography" works.
- The engine's quality check flagged my synthetic plates (`image_not_flat` on 5 of 10, `duplicate_visual` on 7 of 10), so a flat template is not passed off as a picture there.
- **The legacy poster/social path does not make that distinction.** It uses the same drawn dark mountain-silhouette background for every asset of a theme and does not label it as non-AI.
- No curated-image fallback exists (Section 2), so "different photographic assets per concept" cannot be tested.

## 6. Tests D and E: typography and formats (outputs in `cpu_creative_test/out/`)
Contact sheets: `sheet_reels.png`, `sheet_other.png`, `sheet_legacy.png`.

| ID | Asset | Format | Treatment | Headline shown | File |
|---|---|---|---|---|---|
| main1 | Main reel cover | 1080×1920 | cinematic (sans, gold keywords) | What's the Thorn in Your Flesh? | `main1_reel_cover_cinematic.png` |
| main2 | Main reel cover | 1080×1920 | editorial (serif, hairline rules) | Your Weakness is the Key to His | `main2_reel_cover_editorial.png` |
| main3 | Main reel cover | 1080×1920 | fineart (large italic serif) | Bring Your Weakness to the Altar | `main3_reel_cover_fineart.png` |
| short1 | Short reel cover | 1080×1920 | modern (pill label, accent bar) | When your strength fades, His shines brighter | `short1_reel_cover_modern.png` |
| short2 | Short reel cover | 1080×1920 | movie (condensed uppercase) | SURRENDERING ISN'T GIVING UP, IT'S TRUSTING | `short2_reel_cover_movie.png` |
| poster | Highlight poster | 1080×1350 | biblical (serif, ornament) | God's Strength Through Our Weakness | `poster_poster_biblical.png` |
| post1 | Encouragement post | 1080×1350 | emotional | Let Go of Pretense: Embrace Your True Self | `post1_feed_post_emotional.png` |
| post2 | Encouragement post | 1080×1080 | minimal (light cream field) | Honest Prayer: The Key to His Power | `post2_square_post_minimal.png` |
| thumb | YouTube thumbnail | 1280×720 | movie | When Your Strength Ends | `thumb_thumbnail_movie.png` |
| minimal_reel | Typographic cover | 1080×1920 | minimal (engine-native, no plate) | What's the Thorn in Your Flesh? | `minimal_reel_reel_cover_minimal.png` |

**Results:** all four formats rendered at exactly the correct sizes. Spelling was correct, nothing was clipped or overlapping, margins respected the safe areas, and readability was high. The treatments really are different from each other (different fonts, case, rules, layout, and light vs dark), so the engine does not force one template.

**Defects found in typography:**
1. **Mid-phrase truncation:** "Your Weakness is the Key to His Strength" was cut to "…Key to His" to fit 7 words, because `shorten()` falls back to chopping words. The thumbnail "When Your Strength Ends" is a similar cut of a sentence I supplied. The quality check did not stop the dangling "to His" (it only warns).
2. **Weak keyword highlight:** the highlighted gold words included "What's" on the first cover.
3. **Upper half empty on several covers:** by design it is reserved for a picture; without one, the covers look unfinished.
4. **All dark textured backgrounds look alike** across the photo-style covers (the synthetic plates were nearly identical dark fields). That is a limit of having no imagery, not of the layout code.
5. **Legacy poster/posts (ENGINE):** the poster and both posts share one template (dark panel, glowing all-caps white text, same mountain silhouette). They are readable but not visually distinct. The poster's verse line (2 Corinthians 12:9) did not appear.

## 7. Performance (CPU only)
| Operation | Time | Notes |
|---|---|---|
| Planner (2 attempts, both rejected) | 350 s | 86% average CPU; free RAM fell to **0.08 GB**; guard unloaded the model |
| Creative brief (cold model load included) | 61 s | free RAM min 1.3 GB |
| Experimental hooks/poster/posts (model warm) | 31 s | about 5 s per hook-equivalent |
| Typography render, each asset | 3.3–6.0 s | headless Edge, no model |
| Legacy poster + 2 posts via job queue | 23 s total | free RAM min 2.85 GB |
| Model loading | not measured separately | the first LLM call took about 30 s longer than a warm one |
| Python process peak memory | 0.09–0.16 GB | the LLM runs in Ollama (about 2 GB), outside the process |
| Disk used | about 33 MB | mostly output PNGs |
Failed operations: the planner (above). The RAM safety threshold was not changed.

## 8. Quality scores (1–10; my judgement from viewing each image)
Columns: relevance / hook / originality / composition / typography / readability / platform.
| Asset | Rel | Hook | Orig | Comp | Typo | Read | Plat | Notes |
|---|---|---|---|---|---|---|---|---|
| main1 cinematic | 9 | 8 | 3 | 6 | 7 | 9 | 8 | strong question; no picture |
| main2 editorial | 6 | 4 | 3 | 6 | 7 | 8 | 7 | truncated "…Key to His" |
| main3 fineart | 8 | 6 | 4 | 6 | 8 | 8 | 7 | |
| short1 modern | 9 | 7 | 3 | 7 | 8 | 9 | 8 | |
| short2 movie | 9 | 8 | 4 | 7 | 8 | 9 | 8 | |
| poster (new engine) | 9 | 7 | 4 | 7 | 9 | 9 | 8 | |
| post1 | 7 | 6 | 3 | 7 | 7 | 9 | 8 | "true self" drifts from the sermon's "surrender to God" |
| post2 minimal | 9 | 7 | 4 | 7 | 8 | 9 | 8 | |
| thumbnail | 8 | 6 | 4 | 7 | 9 | 10 | 8 | |
| minimal reel cover | 9 | 8 | 4 | 6 | 8 | 9 | 7 | same headline as main1 (duplicate_text flagged) |
| legacy poster | 9 | 7 | 2 | 6 | 6 | 8 | 7 | same template as posts |
| legacy post 1 | 7 | 6 | 2 | 6 | 6 | 8 | 7 | |
| legacy post 2 | 9 | 7 | 2 | 6 | 6 | 8 | 7 | |

**Duplicates and repetition:** background repetition (all dark fields, legacy identical); layout repetition in the legacy path; generic hooks (see Section 3). No asset was excluded for irrelevance, but "Embrace Your True Self" (post 1) is the weakest message match.

## 9. Success criteria
| Question | Result |
|---|---|
| Strong sermon-specific hooks? | **Partly.** The brief's headlines are good; the plan's one-word hooks are generic; the planner failed on a short sermon |
| Visually relevant imagery selected? | **No.** No image model and no curated library; two of three brief metaphors were weak |
| Asset types look genuinely different? | **Yes** in the new studio (10 distinct treatments); **no** in the legacy poster/post path |
| Professional text layouts? | **Mostly yes**; one truncation defect and weak keyword highlighting |
| CPU-only operation stable? | **Partly.** Stable for rendering; the 3B planner pushed free RAM to 0.08 GB and took 350 s without success |
| Unavailable AI identified, not disguised? | **Yes** in the studio and the QC; **not** labelled in the legacy path |

## 10. Recommended improvements (not implemented)
1. Decide the hook policy: allow idea-hooks (3–9 words) in the planner and keep one word only where deliberate; the keyword table currently maps many hooks to "Strength".
2. Make `shorten()` cut at a clause or choose another headline instead of dropping trailing words; make the dangling-end check a QC failure.
3. Tighten the metaphor filter (reject abstract objects such as "closed bag") and add camera perspective and mood to the concept fields.
4. Planner: handle short transcripts (relax or scale the 35–90 s main-reel window) and add a rule-based fallback plan.
5. Label the legacy path's generated backgrounds as templates, and vary background per asset.
6. Add a curated photo/asset workflow if imagery is wanted before GPU hardware is ready.
7. Do not run the 3B planner on this laptop with less than about 3 GB free.

## 11. What requires an NVIDIA GPU
All seven photo-based styles (cinematic, biblical, editorial, emotional, fineart, modern, movie) through diffusers; any practical image generation (CPU generation is disabled by default and would be extremely slow).

## 12. What works reliably on CPU
Brief writing with the 3B model (about 1 minute); typographic design (all treatments render in 3–6 s and pass layout QC); all four output formats; honest "unavailable" reporting; the legacy poster and post pipeline (about 23 s).

## 13. Defer until production hardware (RTX 3060 / 16 GB)
Real image generation and its benchmark; style-by-style visual evaluation; judging whether picture plus typography produces compelling covers; planner and brief quality with a larger LLM; timing for a full batch.

## 14. What was changed
- Created: `cpu_creative_test/` (scripts, isolated data folder, 25 output files) and this report. Nothing existing was overwritten or deleted.
- Not changed: Git branches (`main` at 7f819af, `split-commits` at 19d1bdd), the working tree of tracked files, the six commits, production database (last modified 2026-11-15), configuration, RAM threshold. No dependencies installed, no models downloaded, nothing committed or pushed.
- The new `cpu_creative_test/` folder and this report show up as untracked in `git status`.
