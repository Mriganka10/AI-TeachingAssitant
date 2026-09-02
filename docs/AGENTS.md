# Agent Workflows

## System Shape

Professor AI contains two specialist agents. There is no free-form autonomous supervisor in the
current implementation. The professor explicitly chooses the appropriate assistant from the
dashboard, and the matching route uses a controlled prompt and response contract. Agent execution
is queued as an application job so the browser does not hold a long-running request open.

```text
Professor
   |
   +-- Teaching Assistant
   |      -> queue agent job
   |      -> retrieve notes/books/cases
   |      -> optional web search
   |      -> structured teaching package
   |      -> PPTX, DOCX, PDF, JSON
   |
   +-- Research Paper Assistant
          -> queue agent job
          -> retrieve research papers
          -> optional web search
          -> structured research synthesis
          -> PPTX, DOCX, PDF, JSON
```

## AI Teaching Assistant

Endpoint: `POST /api/agents/teaching`

The endpoint returns a `job_id` quickly. The UI polls `GET /api/jobs/{job_id}` until the job is
`completed` or `failed`.

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
- styled Word teaching handbook with session plan, notes, assessment toolkit, and references
- paginated PDF teaching handbook with matching academic structure
- presentation deck with lecture flow, activities, knowledge checks, and sources

Professor review should check factual accuracy, source suitability, workload, learning-level
alignment, accessibility, and institutional assessment policy.

## Research Paper Assistant

Endpoint: `POST /api/agents/research`

The endpoint returns a `job_id` quickly. The UI polls `GET /api/jobs/{job_id}` until the job is
`completed` or `failed`.

Prompt: `RESEARCH_SYSTEM` in `app/agents/prompts.py`

Default UI collection:

- `research_papers`

Inputs:

- research topic or problem statement
- discipline
- optional additional instructions
- whether to use current web research
- source collections

Required structured output:

- `title`
- `executive_summary`
- `themes`
- `methodology_comparison`
- `research_gaps`
- `research_questions`
- `future_scope`
- `methodology_suggestions`
- `apa_references`
- `sources`

Generated artifacts:

- JSON source representation for audit and interoperability
- styled Word research report with evidence/inference separation
- paginated PDF research report
- presentation deck covering themes, gaps, questions, methodology, and future scope

Professor review should verify every reference, distinguish source evidence from inference, and
validate that a claimed research gap is defensible against the full literature.

## Shared Execution Layer

Both agents use:

1. `AgentJob` for lifecycle tracking.
2. Tenant-scoped retrieval from uploaded documents.
3. `LLMService` for OpenAI or mock generation.
4. `create_artifacts` for export.
5. `StorageService` for local/S3 persistence.
6. `AuditEvent` for success and failure traceability.
7. FastAPI background tasks for the current in-app asynchronous execution model.

## Failure Behavior

- Missing OpenAI key in OpenAI mode marks the queued job as `failed`.
- Invalid model JSON marks the queued job as `failed`.
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
      +-- Teaching/Research Specialist Node
      +-- Citation Verification Node
      +-- Artifact Generation Node
      +-- Professor Review Node
      +-- Audit Node
```

Use explicit state and approval checkpoints rather than allowing uncontrolled autonomous actions.
