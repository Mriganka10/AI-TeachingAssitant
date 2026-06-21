# Models and Agent Techniques

## Current Model Configuration

The model is selected through:

```text
OPENAI_MODEL=gpt-5.5
OPENAI_REASONING_EFFORT=medium
```

Both specialist agents currently share the configured OpenAI model. They differ through their
system prompts, input schemas, selected source collections, and output requirements.

## Component Map

| Component | Current model or technique | Purpose |
| --- | --- | --- |
| Teaching generation | OpenAI Responses API | Generate structured teaching package. |
| Research synthesis | OpenAI Responses API | Generate structured research analysis. |
| Current public research | OpenAI `web_search` tool | Supplement uploaded sources when enabled. |
| Local retrieval | Token-frequency lexical ranking | Select bounded tenant document excerpts. |
| PDF extraction | `pypdf` | Extract text from text-based PDFs. |
| DOCX extraction | `python-docx` | Extract document paragraphs. |
| PPTX generation | `python-pptx` | Build teaching slides. |
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

The service expects one valid JSON object and rejects invalid JSON rather than silently accepting
unstructured output.

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

- JSON Schema or structured-output enforcement
- citation URL and DOI verification
- prompt and model version recording
- output evaluation datasets
- discipline-specific prompt variants
- cost and latency telemetry
- retry behavior for transient model errors
