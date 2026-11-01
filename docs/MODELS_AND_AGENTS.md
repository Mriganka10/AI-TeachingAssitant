# Models and Agent Techniques

## Current Model Configuration

The model is selected through:

```text
OPENAI_MODEL=gpt-5.5
OPENAI_REASONING_EFFORT=low
OPENAI_TEXT_VERBOSITY=high
OPENAI_PROMPT_CACHE_RETENTION=
```

The Teaching Assistant uses the configured OpenAI model with the `TEACHING_SYSTEM` prompt, the
strict teaching JSON schema, and the lecture-notes, books and case-study collections.

The Research Paper Assistant uses the same model in two roles:

- **Reviewer** (`RESEARCH_SYSTEM`, strict `manuscript_review` schema): one call with the numbered
  manuscript and related papers from the `research_papers` collection (plus web search when
  enabled). It is checked for valid locations, a decision consistent with the concerns, unique
  scorecard criteria and evidenced gaps, and gets at most `LLM_REPAIR_ATTEMPTS` repair calls.
- **Librarian** (`LITERATURE_SYSTEM`, `literature` schema, web search): proposes sources; only
  works confirmed in Crossref or arXiv are kept.
- **Planner** (`REVISION_PLAN_SYSTEM`, `revision_plan` schema): keep, revise, expand or add for
  each section, tables, keywords and sources per section.
- **Author-editor** (`REVISION_SYSTEM`, `revised_section` schema): one call per section block, in
  parallel. Deterministic checks (citations, numbers, sources, repetition, length, no AI mentions)
  decide whether a draft is used; one repair call is allowed.
- Review and revision calls use `RESEARCH_REASONING_EFFORT` (default `medium`).

Reviewer text is cleaned of stock phrases and em dashes; manuscript quotes, locations and the
paper's title are never altered. Revised manuscript text gets a lighter, manuscript-safe cleanup
that keeps formal connectives such as "Moreover".

## Component Map

| Component | Current model or technique | Purpose |
| --- | --- | --- |
| Teaching generation | OpenAI Responses API | Generate structured teaching package. |
| Plain-language pass | `app/agents/plain_language.py` | Flag long sentences and filler for one rewrite; apply safe wording fixes. |
| Current public research | OpenAI `web_search` tool | Supplement uploaded sources when enabled. |
| Local retrieval | Chunk-level TF-IDF ranking with a lexical fallback | Select the most relevant bounded tenant document excerpts. |
| PDF extraction | `pypdf` + optional OCR | Extract text PDFs and scanned PDFs when OCR is configured. |
| DOCX extraction | `python-docx` | Extract document paragraphs. |
| PPTX generation | `python-pptx` (`app/artifacts/slides.py`) | Build the student deck from native shapes, with diagrams, icons, transitions and animations. |
| Teaching video | Pillow + FFmpeg + OpenAI or ElevenLabs TTS | Build an optional multimedia MP4 from validated teaching content. |
| Video illustrations | Gemini image model (`GEMINI_IMAGE_MODEL`, optional) | One house-style picture per illustrated scene; text is always drawn by our renderer. |
| Video captions | Pillow + FFmpeg overlay, SRT (`mov_text`) | Burned-in captions below the card plus a switchable subtitle track. |
| PDF generation | ReportLab | Build downloadable reports. |
| Mock testing | Deterministic Python templates | Test without API cost or network dependency. |

## OpenAI Request Shape

`app/agents/llm.py` sends:

- model ID
- system instructions
- JSON-encoded faculty request
- retrieved uploaded context
- optional `web_search`
- reasoning effort for GPT-5-family models
- a stable prompt-cache key for repeated agent contracts
- an optional service tier selected with `OPENAI_SERVICE_TIER`
- a strict agent-specific JSON Schema through Responses API structured outputs
- explicit output verbosity and a bounded output-token budget

The service validates the returned object semantically as well as structurally. It checks lesson
timing, assessment-to-objective links, rubric totals, source identifiers, and topic relevance. A
failed validation receives one bounded repair attempt; an invalid result is never silently
accepted. Plain-language problems (very long sentences, stock filler phrases, self-references,
wordy diagram labels) can also trigger that rewrite, but they never fail a job: if a style-only
rewrite breaks a hard rule, the last valid draft is kept. Every result then passes through safe
wording fixes that never touch URLs, citations, IDs or formulas.

The default reasoning effort is `low` because these are latency-sensitive, schema-constrained
generation tasks. Required fields, citation rules, teaching duration, assessment mappings, and
rubric totals remain protected by the same structured-output contract and semantic validator.
Set `OPENAI_REASONING_EFFORT=medium` only when representative evaluations show a meaningful quality
gain. Accounts with Priority processing enabled can set `OPENAI_SERVICE_TIER=priority`; leaving it
blank retains the account's normal processing tier. Prompt caching is automatic for eligible
requests; projects that support extended caching may additionally set
`OPENAI_PROMPT_CACHE_RETENTION=24h`.

## Execution Pattern

Agent requests are queued as `AgentJob` rows and executed by the SQS-backed ECS worker in
production. The local profile can execute them as FastAPI background tasks. The API
returns a `job_id` immediately; the UI polls `GET /api/jobs/{job_id}` until structured output and
downloadable artifacts are ready. This keeps long OpenAI and document-generation work away from
the original browser request. After validated JSON is committed, DOCX, PDF, and PPTX exporters run
in parallel because they write independent files. When selected, video
generation follows those established exports so narration or encoding failure cannot remove the
existing formats. The browser can display content while those downloads finish.

The video planner makes a separate schema-constrained Responses API call after teaching content is
validated. It receives a student/audience-facing projection of the package, excluding private
facilitation tips, case teaching notes, and internal review priorities. It turns the title,
objectives, lecture sections, examples, case study, and review questions into a coherent 16:9
mini-lecture rather than reading document bullets. The script
must explain definitions, mechanisms, distinctions, examples, misconceptions, and takeaways and
must satisfy a narration word-range check. Narration then uses the configured OpenAI Speech model
and voice. The default duration range is 4–15 minutes, and each dashboard request can select any
positive whole-minute minimum and maximum. The final encoder verifies the requested range. The
storyboard must use at least three visual compositions, including concept maps, comparisons,
processes, timelines, cycles, worked examples, formulas, case studies, or recaps, and it reuses a
lecture section's diagram where one exists. Each scene is rendered as a series of build-up frames,
and FFmpeg `xfade` reveals the diagram one element at a time while it is explained; scenes enter
with animated transitions (slide, wipe, circle) from the previous scene. Narration is written for a
viewer who is new to the topic and says once, in the first scene, that the voice is AI-generated,
as the speech provider's terms require. Narration can use OpenAI TTS or the optional ElevenLabs
long-form provider without changing document generation.

Phase 1 quality layer:

- **Teaching arc.** The script follows hook → everyday analogy → concept → mechanism → worked
  example → misconception → recap, with each scene linked to the one before.
- **Reviewer pass.** A second call rewrites the draft for a complete beginner (terms defined,
  analogies added, filler removed). It keeps facts, scene count and visual types, and it is used
  only if it passes every storyboard check; otherwise the draft is kept.
- **Illustrations.** With `GEMINI_API_KEY` set, the planner adds a picture description to the
  title scene and to `illustration` scenes (hook, analogies, real-world context). Images come from
  the Gemini image model in one house style with no text in them; the renderer adds all labels.
  Requests run in parallel, are capped per video, and any failure leaves that scene's drawn layout.
- **Captions.** Narration is split into short lines timed across each scene's speech, drawn in
  the strip below the card, and also written to a subtitle track in the MP4.

Phase 2 animation layer (`app/artifacts/animations.py`):

- **When.** If Manim is installed and `VIDEO_ANIMATIONS_ENABLED` is on, the planner may mark up
  to `VIDEO_MAX_ANIMATIONS` middle scenes as `animation` (graphs, formulas changing step by step,
  physical processes, algorithms) and describe what moves, in narration order.
- **Timing.** Clips are written after narration is recorded, so the coder gets the exact scene
  length and each caption line's start time and can make each visual appear as it is spoken.
- **Code generation.** A structured call returns one Manim `LessonScene`. A fixed header sets the
  canvas, background and colour palette; the generated code cannot change it.
- **Safety.** Before rendering, the code is parsed and checked against an allow-list: only Manim,
  `math` and `numpy`; no file, network, process or reflection access; no images, SVGs, `while`
  loops or 3D scenes. Rendering runs in a separate process with a time limit, CPU, memory and
  file-size limits, and an environment with no API keys.
- **Verification and repair.** The clip must be at least a second long, not blank, and must
  change over time. A failure (rejected code, render error, timeout or a bad clip) is sent back
  once with the error for a fix.
- **Fallback.** If the repair also fails, the scene keeps its drawn layout (points revealed one by
  one), so animations never stop a video from being made.
- **Composition.** The clip is placed between the scene title and the takeaway, starts when the
  scene's entry transition ends, and holds its last image until the scene ends. The next scene
  enters from that final image.

Phase 3 motion clips (`app/artifacts/cinematic.py`, off by default):

- **When.** With `VIDEO_CINEMATIC_CLIPS=true`, `GEMINI_API_KEY` and illustrations on, the planner
  writes a one-sentence `cinematic` motion description for the title scene and a few
  `illustration` scenes (calm movement of the same picture only, no text or new objects). Up to
  `VIDEO_MAX_CINEMATIC_CLIPS` scenes get a clip, the opening scene first.
- **Generation.** Each request goes to Veo (`VEO_MODEL`, default `veo-3.1-lite-generate-preview`)
  through the Gemini API `predictLongRunning` call, with the scene's illustration as the first
  frame so the clip matches the still. If the model rejects the image, the request is retried as
  text only. Requests start right after the illustrations, run two at a time in the background
  while narration is recorded, and are polled until done.
- **Safety.** A negative prompt excludes text, logos and talking people. Downloads must be HTTPS,
  and the API key is not forwarded if a download redirects to another host.
- **Verification and fallback.** Each clip must be non-blank and moving. A clip that fails,
  is filtered, or is not ready by `VEO_TIMEOUT_SECONDS` is skipped and the scene keeps its still
  picture. Leaving the video early cancels clips that have not started.
- **Composition.** The clip is cropped to fill the picture frame with the same rounded corners,
  starts when the scene's entry transition ends, plays without its own audio, and is slowed by
  up to 1.6x to cover more of the narration before holding its last image.

DOCX and PDF are the teacher's guide, retaining facilitation guidance, answer keys, Bloom
mappings, source limitations, and a "Before You Teach" checklist. PPTX is the student deck: it
omits internal notes, shows each lecture section's diagram (process, cycle, comparison, concept
map, hierarchy or timeline) as native shapes, and reveals knowledge-check answers on the following
slide. Every element animates in automatically, and slides use native transitions. Text sizes are
estimated from content so cards shrink to fit short text and long text keeps whole sentences
rather than being cut mid-word. MCQ labels are normalized across every exporter, and mathematical
notation is rendered with Unicode-capable fonts in PDF, DOCX, and PPTX outputs. Exported files
carry neutral metadata (blank author unless `create_artifacts(author=...)` is given).

## Mode Selection

### OpenAI Mode

```text
LLM_SERVICE_MODE=openai
OPENAI_API_KEY=<valid-key>
```

This is the intended production mode.

### Mock Mode

```text
LLM_SERVICE_MODE=mock
```

Use mock mode for:

- unit and integration tests
- UI development
- demos without external network calls
- validating artifact generation

Mock output is not suitable as academic content.

## Prompt Safety

The current prompts:

- require valid JSON
- prohibit fabricated citations and references
- request use of supplied context
- demand classroom-ready structure for teaching output
- require plain, simple language and one diagram per lecture section

Production quality controls should add:

- citation URL and DOI verification
- prompt and model version recording
- output evaluation datasets
- discipline-specific prompt variants
- cost and latency telemetry
- retry behavior for transient model errors
