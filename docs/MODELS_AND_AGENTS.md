# Models and Agent Techniques

## Current Model Configuration

The model is selected through:

```text
OPENAI_MODEL=gpt-5.5
OPENAI_REASONING_EFFORT=low
OPENAI_TEXT_VERBOSITY=high
OPENAI_PROMPT_CACHE_RETENTION=
```

Both specialist agents currently share the configured OpenAI model. They differ through their
system prompts, input schemas, selected source collections, and output requirements.

## Component Map

| Component | Current model or technique | Purpose |
| --- | --- | --- |
| Teaching generation | OpenAI Responses API | Generate structured teaching package. |
| Research synthesis | OpenAI Responses API | Generate structured research analysis. |
| Current public research | OpenAI `web_search` tool | Supplement uploaded sources when enabled. |
| Local retrieval | Chunk-level TF-IDF ranking with a lexical fallback | Select the most relevant bounded tenant document excerpts. |
| PDF extraction | `pypdf` + optional OCR | Extract text PDFs and scanned PDFs when OCR is configured. |
| DOCX extraction | `python-docx` | Extract document paragraphs. |
| PPTX generation | `python-pptx` | Build teaching and research presentation decks. |
| Teaching video | Pillow + FFmpeg + OpenAI or ElevenLabs TTS | Build an optional multimedia MP4 from validated teaching content. |
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
timing, assessment-to-objective links, rubric totals, source identifiers, and evidence for research
gaps. A failed validation receives one bounded repair attempt; an invalid result is never silently
accepted.

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
in parallel because they write independent files. When selected for a teaching job, video
generation follows those established exports so narration or encoding failure cannot remove the
existing formats. The browser can display content while those downloads finish.

The video planner makes a separate schema-constrained Responses API call after teaching content is
validated. It turns the title, objectives, lecture sections, examples, activities, case study, and
review questions into a coherent 16:9 mini-lecture rather than reading document bullets. The script
must explain definitions, mechanisms, distinctions, examples, misconceptions, and takeaways and
must satisfy a narration word-range check. Narration then uses the configured OpenAI Speech model
and voice. The default duration range is 4–15 minutes; a final encoder limit guarantees that no
video exceeds 15 minutes. The storyboard must use at least three visual compositions, including
concept maps, comparisons, processes, worked examples, formulas, case studies, or recaps. The UI
and video both disclose that the narration is AI-generated. Narration can use OpenAI TTS or the
optional ElevenLabs long-form provider without changing document generation.

The presentation is intentionally a concise teaching or research deck. Full prose remains in the
DOCX, PDF, and JSON outputs. Slide layouts summarize long explanations, preserve assessments and
source lists, clean Markdown links, and paginate only when content cannot fit at presentation size.
PPTX callouts use consistent visual grouping, MCQ labels are normalized across every exporter, and
mathematical notation is rendered with Unicode-capable fonts in PDF, DOCX, and PPTX outputs.
Callout heights, answer areas, and numerical-problem regions are calculated from their content;
PowerPoint callouts calculate their natural height, select a readable fitting font, and allocate
separate vertical regions. Dense professor guidance moves to a continuation slide rather than
overlapping another box. PowerPoint text fitting remains enabled as a final safeguard.

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
- distinguish evidence from inference for research output
- demand classroom-ready structure for teaching output

Production quality controls should add:

- citation URL and DOI verification
- prompt and model version recording
- output evaluation datasets
- discipline-specific prompt variants
- cost and latency telemetry
- retry behavior for transient model errors
