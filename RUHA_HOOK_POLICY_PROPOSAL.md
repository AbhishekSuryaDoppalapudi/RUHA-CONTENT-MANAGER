# RUHA — Hook Policy: Technical Proposal

Status: **proposal only. Nothing was implemented.** Line numbers refer to the working tree on 2026-11-16 (`main` at 7f819af plus uncommitted changes; the same code is in branch `split-commits`).
Hook text shown in this document is **illustrative only**. No example is a default, template or phrase library, and none may be hardcoded.

## 0. Decisions recorded (2026-11-16, from the product owner)
| Question | Decision | Effect on this proposal |
|---|---|---|
| Hook length for shorts vs main reels | **Shorts get a tighter default, and it is a different limit from main reels** | Two separate settings, `hook.max_chars_main` and `hook.max_chars_short` (words or characters, whichever the measurement supports). Values are set from card-fit measurement (Section 8), not guessed. Both are defaults the editor can override per reel with a warning, not a hard block. Applies to validation and to the model's instructions; the clip category (`main`/`short`) is already known in `h_reels` (`handlers.py:450-451`) |
| Hook language for translated reels | **English for now; keep an option to choose other languages** | Hook language setting `hook.language` (default `English`). No automatic translation of hooks. The option is implemented as an explicit choice per reel in the editor (and a global default), reusing `_translate_lines` (`handlers.py`) only when a non-English language is selected; the English hook stays stored as `hook_original`/`hook`, the localized text goes in a new optional `hook_localized: {language: text}`. Highlights for non-English text are model/manual only (Section 7) |
| Rename asset when the hook is edited | **Yes, unless the user changed the title separately** | Title follows the hook while `title == previous hook[:200]`; a manually edited title is never overwritten |
| Compare new hooks with other sermons | **Yes, compare and make them unique** | `memory.similar` is used across projects at generation and at edit time; a hook too close to an earlier sermon's hook is rejected or regenerated (and flagged `duplicate_of_other_sermon` in the editor, where a manual edit may still override it with a visible warning). The similarity threshold needs tuning with real data |
| Candidate generation timing | **Decide later (until RTX 3060 is ready)** | Stays on-demand only (editor button); the automatic mode is not built now, only the setting key reserved |

## 1. Approved policy as implementation requirements
| # | Requirement | Meaning in code |
|---|---|---|
| R1 | Idea-first is the default | A reel's card text is the full generated hook; no automatic shortening anywhere in the plan or render path |
| R2 | Hooks are generated per sermon | Derived from the clip's own transcript text plus the sermon's message, themes and audience; no example phrases in prompts or code |
| R3 | One word is optional | A separate, explicitly chosen treatment (`hook_treatment = "short"`), never applied silently |
| R4 | Semantic highlighting | Highlight words come from meaning (model choice, validated, plus a better fallback) or from the user; not from word length |
| R5 | Preserve originals | Four stored representations (Section 5); shortening never overwrites the original |
| R6 | Validation | Content validation and layout validation are separate (Section 7) |
| R7 | Backward compatibility | No migration; old plans and assets untouched until a user acts (Section 10) |
| **R8** | **Manual editing (added by you)** | Reels get a hook-text editor: edit hook, optional short version, highlight words, closing line, preview, then apply (Section 6) |

## 2. Current hook architecture (as found)
```
transcript segments (DB)
 -> handlers.ensure_plan            handlers.py:68-93   (re-plans only if plan lacks visual_theme or reel counts differ, :74-77)
 -> planner.create_plan             planner.py:322-343  (2 attempts, feeds rejection reason back)
      short talks (<= 22,000 chars, SINGLE_PASS_CHARS :11): ONE prompt over the FULL transcript   _single_prompt :132
      long talks: per-chunk _map_prompt :138 (asks "moments" with hooks), then _reduce_prompt :146 which sees only a JSON DIGEST
                  (summaries, themes, quotes, moments), not the transcript
 -> planner.normalize_plan          planner.py:239-302
      reel hooks:  [:120] truncation (:275), then one_word_hook() overwrites r["hook"] (:296-300)   <-- ORIGINAL IS DESTROYED
      poster hook: [:140] (:292-293);  post hooks: non-empty check + [:160] (normalize_posts :305-319)
 -> plan_json stored in transcripts.plan_json
 -> handlers.h_reels                handlers.py:439-499
      _reel_cards :205-220  re-applies one_word_hook() AGAIN at render time (:213-214)
      render_card design.py:397 -> htmlrender.render_card htmlrender.py:282 -> _hook_html :146 -> _key_words :140
      asset title = hook[:200] (:483), reels.hook = hook (:491), asset meta has "closing" but no hook fields
 -> re-render path h_render handlers.py:669-720 reads reels.hook (:697) and calls _reel_cards (:701)
```
**Answers to the questions asked:**
- *Full transcript, segments or only the plan?* Short talks: the full transcript in one prompt. Long talks: reel hooks come from per-chunk "moments", then a digest-only reduce step. There is no per-clip pass that reads the exact words of the chosen clip.
- *Hook-quality validation today:* none beyond truncation and one-word collapse. No faithfulness check, no duplicate check across reels/poster/posts, no layout check after rendering.
- *Where hooks are shown or reused:* reel cards (design.py:397, htmlrender.py:282), asset title and library (`handlers.py:483`), `assets.py:167` (API), Creative Studio fallback headlines (`creative/brief.py:72-80`) and its source panel (`api/creative.py:59`), image reels (`handlers.py:256`, user-typed, never collapsed).

## 3. Existing one-word behaviour
- **Prompt schema forces it:** `planner.py:110-111` ask for `"hook":"ONE bold word only, e.g. Battle"` / `e.g. Grace`. Line 123 (HOOK TEXT rule) also embeds two literal example hooks.
- **`one_word_hook` (`planner.py:89-99`):** keeps a genuine single word; otherwise returns the first match from `_HOOK_WORDS` (`:69-82`, 12 regex→word rules, first match wins), else the theme word (`_THEME_WORD :83`), else the literal `"Listen"`.
- **Applied twice:** at plan time (`:296-300`, destroying the original in the stored plan) and again at render time (`handlers.py:213-214`).
- **Measured effect (CPU test, 8 idea-hooks):** six became "Strength", one "Faith", one "Pray", because the regex `weak\w*|strong|...` outranks everything else. Any sentence containing "storm" or "still" becomes "Peace"; "free" becomes "Freedom"; the fallback "Listen" is itself in `_WEAK_WORDS`.
- **Existing tests that encode the old rule:** `tests/test_unit.py:148-150`.

## 4. Affected code (smallest coherent change)
| Component | File : lines | Change | Size |
|---|---|---|---|
| Plan schema + rules text | `planner.py:108-131` (`PLAN_SCHEMA`, `_rules`) | hook = idea-first line; add optional `hook_short` only when one word carries the meaning; remove the literal example hooks and "ONE bold word" wording; describe qualities instead | S |
| Map/reduce prompts | `planner.py:138-153` | `moments[].hook` same wording; reduce step must keep hooks from the clip's own text | S |
| Normalisation | `planner.py:275`, `:296-301` | stop calling `one_word_hook`; fill `hook_original`, run content validation; keep `[:120]` only as a safety ceiling | M |
| One-word helper | `planner.py:66-99` | keep as an **optional suggestion function** (`suggest_short_hook`) that returns a candidate and a reason; never writes `hook`; the keyword table stops being a fallback | S |
| Hook candidates (new) | new `ruha/services/hooks.py` | candidate generation, validation, dedupe, fallback (Section 8) | M |
| Render-time resolver | `handlers.py:205-220` `_reel_cards` | replace `one_word_hook(...)` by `hooks.card_text(clip)` (see Section 5); pass highlight marks | S |
| Reel registration | `handlers.py:439-499` `h_reels`, `:669-720` `h_render` | store the hook fields in asset meta; `h_render` reads edited fields | M |
| Highlighting | `htmlrender.py:51-60` (`_STOP`), `:140-152`; duplicates in `design.py:111-120` and `creative/compose.py:151-163` | one shared function; `_hook_html(text, marks=None)`; `compose._hl_html` and `design` use it | M |
| Card fit feedback | `htmlrender.py:225-234` (`_FIT_JS`), `:282-312` | return the final scale/overflow so the layout can be validated; today it silently shrinks to 42% | M |
| Pillow fallback card | `design.py:397-440` (`block(...)` min size 56) | accept marks; uppercase rule only where intended | S |
| Manual edit API | `api/assets.py:43-49` (`AssetPatch`), `:109-134` (`patch_asset`), `:163-189` (`regenerate`) | add `reel` fields; enqueue `video_rendering` when they change; add hook preview and "suggest hooks" endpoints | M |
| Asset edit service | `services/assets.py:13`, `:88-111` | no change to `EDITABLE`; hook fields live in meta; editing an approved asset already returns it to draft (`:105-106`) | S |
| Creative Studio | `creative/brief.py:38-53` (`shorten`), `:72-80`, `:108-198` | replace last-resort word-chop with "pick another candidate / regenerate to fit"; take approved hooks from the plan; mark `hook_source` | S |
| Frontend | `AssetDetail.tsx` (new "Hook & text" panel for reels), `api.ts:89` (`reel` type), `Settings.tsx` | editor (Section 6), settings | M |
| Config | `config.py:117` area | `hook.style`, `hook.candidates`, `hook.min_fit` (Section 6) | S |
| Tests | `tests/test_unit.py:148-150`, `conftest.py:~60-100` (FakeLLM), new `tests/test_hooks.py` | Section 8 | M-L |

Not affected: `h_image_reel` (hook is user text), poster/social text editing (already editable via `PATCH` poster/social fields, `AssetDetail.tsx:34-39`), subtitles, cutaways, BrandKit.

## 5. Proposed data structure (no schema migration)
Stored inside the existing free-form JSON (`transcripts.plan_json` per clip, and `generated_assets.meta_json` per reel):
```
clip = {
  "hook":            "approved wording shown by default (full idea-first line)",
  "hook_original":   "exactly what the model produced; written once, never edited",
  "hook_short":      "optional shortened text for the card (may be one word); empty when unused",
  "hook_treatment":  "full" | "short",          # default "full"
  "hook_highlight":  ["word", ...],              # semantic selection; each must be a token of the text shown
  "hook_highlight_source": "model" | "auto" | "manual",
  "hook_source":     "llm" | "llm-repaired" | "fallback-quote" | "manual",
  "hook_candidates": [{"text": "...", "angle": "question|contrast|takeaway|...", "evidence": "clip words that support it"}],
  "hook_flags":      ["needs_review", "duplicate_of:<id>", "overflow", ...]
}
```
- `hook` keeps its name so every existing reader (`Reel.hook`, API, memory index, Creative fallback) still works.
- `hooks.card_text(clip)` = `hook_short` if `hook_treatment == "short"` and `hook_short` else `hook`. Reversible: switching treatment never loses text.
- Old clips have only `hook`: treated as approved text, `hook_original = hook`, treatment `full`.

## 6. Manual editing of reels and hook text (new requirement)
**What already exists:** posters and social posts have a headline editor that re-renders on save (`api/assets.py:117-130`, `AssetDetail.tsx:34-39,95-100`); Creative Studio has a headline editor (`creative/studio.py:314-317`). **Reels have no hook editor**: `PATCH` changes only title/caption/hashtags, and `h_render` ignores the title and reads `reels.hook`. Hook text on a reel cannot currently be edited at all.

**Proposed editor ("Hook card" panel in the reel's detail view):**
1. *Hook text* field (approved wording).
2. *Short version* field plus a "Use short version on the card" switch (off by default).
3. *Highlight words*: the hook's words as clickable chips; click to toggle; an "Auto" button restores the suggested selection.
4. *Closing line* field (already stored in meta, `handlers.py:488`).
5. *Preview*: renders only the card (about 4 s on this laptop) without re-encoding the video. New endpoint `POST /api/assets/{id}/hook-preview`.
6. *Apply to reel*: saves, then queues `video_rendering` (existing job, `h_render`) so a new version is produced; the previous version stays in history and can be restored (`api/assets.py:149`). An approved reel returns to "draft" automatically (`services/assets.py:105`).
7. *Reset to original* restores `hook_original`; *Suggest hooks* (Section 8) lists alternatives from the clip's own transcript excerpt (`reels.transcript_excerpt` exists), nothing is applied until chosen.
8. Warnings from validation appear inline (empty, too long to read, not in the clip's words, duplicate of another asset); a warning never blocks a manual edit except empty text.
Editing the hook also keeps the library title in step with it unless the user has changed the title separately.

**Settings (Settings page, export group):** `hook.style` (`idea` default | `short` | `ask-per-reel`), `hook.candidates` (0 = off by default on this CPU laptop, 3 = generate alternatives automatically), `hook.min_fit` (readability floor, set from measurement, Section 7).

## 7. Semantic highlighting (proposal)
Today: the two longest words over four letters that are not in a small stop list (`htmlrender.py:140-143`). Defects seen: contractions such as "What's" are highlighted because only the bare word "what" is in the stop list; length decides meaning.
Proposal:
1. **Model choice first.** The hook prompt asks for 1–2 highlight words and the reason class (contrast, emotional, key idea, action). Code keeps only words that are tokens of the exact text shown.
2. **Validated:** reject articles, conjunctions, prepositions, auxiliaries, pronouns, question words and contractions unless the model gives a creative reason; a larger stop list covering contractions.
3. **Fallback when the model gives none (deterministic, no new dependency):** score content words by presence in the plan's `themes` and `key_points`, position (after a negation or contrast marker such as "not X, but Y"), then length only as a tie-break. Maximum 2 highlights (or one short phrase).
4. **Manual override** from the editor chips; `hook_highlight_source = "manual"` is never recomputed.
5. One shared implementation used by the HTML card, the Pillow fallback and Creative Studio; non-Latin scripts keep whole-word marking from the user/model (no length heuristic).

## 8. Validation (content vs layout) and candidate generation
### Content validation (no rendering needed)
| Rule | How |
|---|---|
| Not empty | trimmed text non-empty (existing check for posts only, `planner.py:315`) |
| Not meta-narration | reuse `BAD_OPENERS` (`creative/brief.py:19`), e.g. "In this sermon…" |
| Faithful to the clip | (a) at least one content word of the hook appears in the clip's transcript excerpt or the plan's themes; (b) scripture references stripped unless spoken (reuse `brief._strip_unspoken_refs`); (c) embedding similarity to the excerpt using the existing embedder (`memory`) as an advisory flag. Needs real-model tuning, so it flags, not rejects |
| Not silently replaced | no code path may write `hook` from the keyword table; shortening writes `hook_short` only |
| One word only when chosen | single-word `hook_short` requires explicit selection (user or model with a stated reason) |
| Highlights belong to the text | each mark must be a token of the text shown |
| Not repeated | `brief.similar` (`creative/brief.py:31`) across main, shorts, poster and posts of the same sermon; optional `memory.similar` against other sermons so a phrase is not recycled project to project |
### Layout validation (after rendering)
`_FIT_JS` (`htmlrender.py:225-234`) already shrinks the text until it fits down to 42% of 118 px. Add a returned measurement (final scale, line count, overflow) and flag when the text needed to fall below a readability floor. **I recommend deriving the floor and any length limit from measurement on this renderer (and the existing Creative `mobile_readability` check), not from an invented word count.** Existing format guides: `formats.py:9-19` (`max_words` 7 for reel/hook covers, 9 feed, 8 square, 5 thumbnail) apply to Creative Studio only; reel cards have no limit today except the 120-character ceiling.

### Producing several distinct candidates without repeating one idea
1. Keep the main plan call (it already reads the full transcript for short talks) but change its hook instruction.
2. Add an **on-demand, per-clip candidate call** (`hooks.suggest(clip_text, sermon_context)`): input is the clip's exact words plus the sermon message, themes and audience. Output: up to 4 candidates, each with an `angle` and an `evidence` snippet copied from the clip. Angles allowed: curiosity, emotional connection, challenging question, contrast, practical takeaway, faith insight; **an angle is returned only when the clip supports it**.
3. Code then validates, removes near-duplicates (`similar`), prefers angle diversity, and sets the first valid one as `hook`, the rest as `hook_candidates`.
4. On this CPU laptop the extra call costs roughly 30–60 s per clip (measured: 31 s for a 5-hook prompt), so it should be on demand (editor button) by default and automatic only on the RTX 3060 machine (`hook.candidates`).
5. Do not put example hooks in the prompt; describe qualities and pass sermon-specific context so a small model has nothing to copy.
### Fallback when the model's output is invalid
1. Existing plan retry (`create_plan`, 2 attempts). 2. A small hook-repair call for just the failing hook. 3. Deterministic fallback: shorten, at a clause boundary, a verbatim quote or key point from that clip's own range (`hook_source = "fallback-quote"`, flagged `needs_review`). 4. Last resort: the sermon title. **Never a keyword-table word.** The plan is not failed because of a hook.

## 9. Test plan
Use mocked LLM output (extend `FakeLLM`, `tests/conftest.py:~60-100`); render tests use the real headless browser at small size as the current suites do.
| # | Case | Where | Real-model check? |
|---|---|---|---|
| 1 | Strong idea-first hook preserved through normalise → card | `test_hooks.py` | no |
| 2 | Hook derives from this sermon's content: two different mocked sermons give different hooks; none equal any phrase in the code or prompts (scan source for the example strings) | `test_hooks.py` | **yes** (3B quality) |
| 3 | One-word treatment applies only when `hook_treatment="short"` | `test_hooks.py` | no |
| 4 | `hook_original` and `hook` unchanged after shortening and after un-shortening | `test_hooks.py` | no |
| 5 | Semantic highlight picks the meaningful word, e.g. a contrast or theme word in a mocked hook | `test_highlight.py` | partly |
| 6 | Fillers, articles, question words and contractions (e.g. "What's") are never highlighted | `test_highlight.py` | no |
| 7 | Empty, "In this sermon…", and unrelated hooks rejected or flagged | `test_hooks.py` | embeddings advisory: yes |
| 8 | A sentence containing "storm" or "weak" is **not** replaced by a keyword | `test_hooks.py` (replaces `test_unit.py:148-150`) | no |
| 9 | Main reel cover renders (hook card of a main reel) | `test_pipeline_e2e.py` / new | no |
| 10 | Short reel cover renders | same | no |
| 11 | Long hook: card text fits or is flagged `overflow`; Pillow fallback also fits | `test_design.py` | no |
| 12 | Different topics (e.g. mocked grace vs. warfare) → distinct hooks, no repeated hook across assets | `test_hooks.py` | **yes** |
| 13 | LLM returns invalid JSON/empty hook → repair → fallback quote; plan not failed | `test_hooks.py` | **yes** |
| 14 | Existing reel generation unchanged: old plan with one-word hooks renders identically; `ensure_plan` does not re-plan because new keys are missing | `test_pipeline_e2e.py`, `test_unit.py` | no |
| 15 | Editor API: patch hook/short/highlights/closing → re-render queued, new version, previous version restorable, approved → draft, preview endpoint writes no asset | `test_api_*` | no |
| 16 | Editor UI: chips toggle, reset to original, validation messages | frontend check in browser | manual |
Also update the stale assertion at `tests/test_unit.py:148-150`.

## 10. Backward compatibility
- **Stored plans:** new keys are optional; old clips are read as `hook` only. **Do not** add the new keys to the "plan is current" test (`handlers.py:74-77`), or every old project would trigger an LLM re-plan.
- **Existing reels and cards on disk:** untouched. `reels.hook` stays as stored. A card changes only if a user re-renders (job `video_rendering`) or edits the hook.
- **Old one-word hooks:** the original sentence is gone from stored plans. They are treated as approved text. The editor's "Suggest hooks" can propose idea-first alternatives from `reels.transcript_excerpt` without changing anything until chosen.
- **Re-render difference:** an old plan that stored a sentence would now show the sentence instead of being collapsed at render time (`handlers.py:213-214`); this only appears on an explicit re-render.
- **Database:** no migration (JSON fields and the existing `reels.hook` column). **Approved assets** are never overwritten: edits create new versions and send the asset back to draft.
- **Creative Studio and posters/posts:** already idea-first; only the last-resort word-chop is replaced.

## 11. Risks and unresolved questions
1. **Small-model quality:** a 3B model may repeat itself or produce weak hooks; the candidates call and validation reduce but do not remove this. Needs real-model evaluation on at least three different sermons.
2. **Card capacity:** long hooks on a 1080×1920 card shrink to as little as 42%; the readability floor and any limit must be measured before they are set.
3. **Shorts vs main reels:** should shorts get a tighter default length? Needs your decision.
4. **Translated reels:** hooks are not localized today (cards use the English text). Should the hook be translated with the captions?
5. **Highlight for non-Latin scripts** (Telugu, Hindi): relies on model or manual choice; no heuristic.
6. **Faithfulness check** is advisory with embeddings; stricter checks risk rejecting good hooks.
7. **Latency on CPU:** automatic candidates only on production hardware.
8. **Title sync:** whether editing the hook should rename the asset automatically (proposed: yes unless title was edited).
9. **Memory/de-dup across sermons:** repeated hooks across projects need `memory.similar`; confirm you want that comparison.
10. **Planner short-transcript failure** (seen in testing, and possibly job #37) is separate but blocks hook work on short talks; fix it independently.

## 12. Recommended implementation sequence
1. `hooks.py` data structure and `card_text()` resolver with legacy handling, plus tests 3, 4, 14 (S).
2. Remove forced collapse (`normalize_plan`, `_reel_cards`), new prompt wording without examples, content validation and fallback; tests 1, 2, 7, 8, 13 (M).
3. Shared highlighter and marks through `htmlrender`, `design`, `compose`; tests 5, 6 (M).
4. Card fit measurement and layout flags; measure the readability floor; tests 9–11 (M).
5. Editor API (patch, preview, reset) and reel re-render wiring; test 15 (M).
6. Frontend "Hook card" panel and Settings keys; manual test 16 (M).
7. On-demand candidate generation and duplicate check; test 12 (M).
8. Creative Studio `shorten` fix and use of approved hooks (S).
9. Real-model validation on three sermons; docs and changelog.

## 13. Estimated complexity
| Component | Size | Rough effort |
|---|---|---|
| Data structure + resolver | S | 2–3 h |
| Planner prompt/normalise/fallback/validation | M | 6–8 h |
| Highlighter | M | 4–6 h |
| Card layout measurement | M | 4–6 h |
| Editor API + render wiring | M | 5–7 h |
| Editor UI + settings | M | 6–8 h |
| Candidate generation | M | 5–7 h |
| Creative Studio change | S | 1–2 h |
| Tests (new + updates) | M–L | 8–12 h |
| Real-model validation | M | 3–4 h, plus a machine with enough free RAM |
Total roughly 45–60 hours of work; the planner and card-fit steps carry the most uncertainty.
