# Agent Workflows

## System Shape

Professor AI contains two specialist agents: the **AI Teaching Assistant** and the **Research
Paper Assistant**. There is no free-form autonomous supervisor. Each route uses a controlled prompt
and a strict response contract, and work is queued as an application job so the browser does not
hold a long-running request open. Jobs with any other agent type fail cleanly.

```text
Professor
   |
   +-- Teaching Assistant
          -> queue agent job
          -> retrieve lecture notes / books / case studies
          -> optional web search
          -> structured teaching package (plain-language rules, one diagram per section)
          -> teacher's guide (DOCX, PDF), student slides (PPTX), JSON, optional narrated MP4
   |
   +-- Research Paper Assistant
          -> queue agent job
          -> parse the manuscript into numbered sections and paragraphs (S3, S3.P2)
          -> retrieve related papers from the research library (manuscript excluded)
          -> optional web search for recent literature
          -> structured peer review (locations checked against the manuscript)
          -> section-by-section revision with integrity checks
          -> review report (DOCX, PDF), revised paper (DOCX, PDF, tracked-changes DOCX), JSON
```

## Research Paper Assistant

Endpoint: `POST /api/agents/research`. Upload the manuscript first with `POST /api/documents`
(collection `manuscripts`), then pass its `id` as `manuscript_document_id`.

1. **Structure.** `app/agents/manuscript.py` rebuilds paragraphs from the extracted text (wrapped
   PDF lines or one-line Word paragraphs), removes page numbers and running headers, finds the
   title, front matter, abstract, numbered and named section headings, back matter, references
   and appendices, and gives every section and paragraph an ID. A paper without headings is split
   into parts. Text beyond `RESEARCH_MAX_MANUSCRIPT_CHARS` is not reviewed, and the report says so.
2. **Review.** One structured call (`RESEARCH_SYSTEM`, `RESEARCH_SCHEMA`) returns the review. The
   semantic checks reject locations that do not exist in the manuscript, a recommendation that
   contradicts the concerns (for example "accept" with high-priority concerns), duplicate scorecard
   criteria and research gaps without evidence. Style checks (soft, never fatal) remove AI
   self-references and stock phrases. Related papers come from the `research_papers` collection.
3. **Revision** (`app/agents/revision.py`):
   - *Sources.* With "Add recent literature" on, a web-search call proposes recent and seminal
     works for the review's gaps. `app/agents/sources.py` confirms each DOI in Crossref or arXiv ID
     in arXiv, checks the registered title matches, drops works the paper already cites, and keeps
     the registry's metadata. Unconfirmed works are never cited.
   - *Plan.* A planning call decides for every body section whether to keep, revise or expand it,
     which sections to add (at most `RESEARCH_MAX_NEW_SECTIONS`), where tables help (at most three),
     the keywords and which verified sources each section cites. The plan is normalised so every
     original section appears exactly once.
   - *Write.* Sections are written in parallel (`RESEARCH_REVISION_WORKERS`, blocks of at most
     `RESEARCH_REVISION_MAX_WORDS` words). New sources are cited as `[[N1]]` markers and tables as
     `[[TABLE1]]`. Each block must keep every original citation and number, cite nothing outside the
     manuscript and the verified sources, use no number not found in the manuscript or a cited
     source, avoid repeated sentences, stay within a sensible length and never mention AI. One
     repair is allowed; then a revised section keeps its original text and an added section is
     dropped, and the job warning names them.
   - *Assemble.* Headings are renumbered when the paper numbers them; markers become the paper's
     style (numeric `[n]` continuing after the last reference, or author-year); new references are
     formatted in the list's own style (IEEE or APA) and merged without reordering numbered lists;
     tables are numbered; the abstract is revised last with keywords.
4. **Exports.** `app/artifacts/research_docs.py` writes the review report (ending with the change
   summary, the references added, author items, a before-you-submit checklist and an AI-assistance
   declaration the author can adapt), the clean revised paper (numbered headings, keywords,
   three-line captioned tables) and a Word copy whose edits, including new sections, tables and
   references, are real tracked changes.

Job progress: `content_ready` means the review is written and the revision is in progress;
`completed` means every file is ready.

## AI Teaching Assistant

Endpoint: `POST /api/agents/teaching`

The endpoint returns a `job_id` quickly. The UI polls `GET /api/jobs/{job_id}` without imposing a
short browser-side timeout. Validated content becomes visible at `content_ready`; export creation
continues until the job is `completed`, `artifact_failed`, or `failed`. When video is selected,
`documents_ready` exposes JSON, DOCX, PDF, and PPTX downloads while MP4 rendering continues.

Prompt: `TEACHING_SYSTEM` in `app/agents/prompts.py`

Default UI collections:

- `previous_notes`
- `books`
- `case_studies`

Inputs:

- topic
- course
- audience
- session duration
- difficulty
- optional additional instructions
- whether to use current web research
- whether to generate a multimedia explainer video
- preferred minimum and maximum video duration as positive whole minutes
- source collections

Required structured output:

- `title`
- `overview`
- `learning_objectives`
- `lecture_sections`
- `mcqs`
- `assignments`
- `numerical_problems`
- `bloom_mapping`
- `discussion_questions`
- `viva_questions`
- `case_study`
- `sources`

Generated artifacts:

- JSON source representation for audit and interoperability
- **Teacher's guide (DOCX and PDF)**, the teacher's private copy: session plan with timings,
  teaching notes, answer keys with the objective and difficulty each MCQ assesses, model viva
  answers, Bloom's mapping, references, and a "Before You Teach" checklist of points to verify
- **Student slides (PPTX)** built by `app/artifacts/slides.py` from native, editable PowerPoint
  shapes: a cover illustration, one diagram per lecture section (process, cycle, comparison,
  concept map, hierarchy or timeline), drawn icons, worked examples, "watch out" misconception
  cards, knowledge checks with a separate answer slide, rubric bars, a recap and further reading.
  Slides carry native transitions and entrance animations that play automatically. Teacher-only
  material (tips, timings, Bloom levels, expected answers, review notes) never appears on slides
- optional 16:9 **narrated MP4** derived from the validated teaching package. A second structured
  storyboard pass receives only learner-facing material and turns it into a mini-lecture for a
  viewer who is new to the topic. Each scene's diagram builds up one element at a time while it is
  explained, and scenes change with animated transitions (slide, wipe, circle). The narration says
  once, in the first scene, that the voice is AI-generated, as the speech provider's terms
  require. Duration is verified against the professor's selected range (default 4–15 minutes);
  the renderer trims silent TTS tails, includes every storyboard scene, and checks visual changes
  in the finished MP4 before making it downloadable.

Writing style: the prompt asks for plain language (short sentences, everyday words, every term
defined, a concrete example for each idea). `app/agents/plain_language.py` flags overlong
sentences, stock filler phrases and self-references for one model rewrite, which never fails a job,
then applies safe wording fixes. URLs, citations, IDs and formulas are never changed. Exported files
carry neutral metadata (no tool or product name); `create_artifacts(author=...)` can set the
teacher's name.

All visual exporters normalize MCQ option labels before numbering them, so an option is rendered
once as `A. option text` even if a model response includes its own prefix. Mathematical notation is
normalized to portable Unicode; PDF output embeds a Unicode font, and DOCX/PPTX math runs use a
math-capable font so symbols such as `ŷ`, `Σ`, `μ`, `≈`, subscripts, and superscripts remain legible.

Professor review should check factual accuracy, source suitability, workload, learning-level
alignment, accessibility, and institutional assessment policy.

## Shared Execution Layer

The agent uses:

1. `AgentJob` for lifecycle tracking.
2. Tenant-scoped retrieval from uploaded documents.
3. `LLMService` for OpenAI or mock generation.
   Before export, semantic validation checks that the title and substantive sections retain
   the professor's requested topic. A drifted model response receives the configured repair
   attempt and remains a failed job if it still does not match.
4. A `content_ready` checkpoint that exposes validated JSON before export work completes.
5. `create_artifacts` for established document export, plus optional teaching-video rendering.
   The optional video storyboard is also checked against the validated teaching title and lesson
   sections before narration begins.
6. `StorageService` for local/S3 persistence.
7. `AuditEvent` for success and failure traceability.
8. SQS-backed ECS workers in production, with FastAPI background tasks only for local development.

## Failure Behavior

- Missing OpenAI key in OpenAI mode marks the queued job as `failed`.
- Invalid model JSON marks the queued job as `failed`.
- Export failures preserve validated content and mark the job as `artifact_failed`.
- Optional video failures preserve JSON, DOCX, PDF, and PPTX, complete the job, and return a warning.
- Mock mode never creates a misleading silent placeholder video; narrated video requires
  `LLM_SERVICE_MODE=openai` and `OPENAI_API_KEY`.
- Upload extraction errors return a client error and do not create a ready document.
- Artifact access from another tenant returns 404.

## Recommended Future Agent Design

When workflows require iterative tool use, approvals, or long-running execution, evolve to:

```text
Faculty Request
      |
      v
Workflow Supervisor
      |
      +-- Source Ingestion Node
      +-- Retrieval Node
      +-- Web Research Node
      +-- Teaching Specialist Node
      +-- Citation Verification Node
      +-- Artifact Generation Node
      +-- Professor Review Node
      +-- Audit Node
```

Use explicit state and approval checkpoints rather than allowing uncontrolled autonomous actions.
