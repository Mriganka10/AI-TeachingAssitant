# Professor AI Workspace

A deployable, multi-tenant workspace for professors with two assistants.

**AI Teaching Assistant.** From one topic and the professor's own course materials it prepares a
complete lesson:

- **Teacher's guide** (DOCX and PDF): session plan with timings, teaching notes, answer keys,
  model answers, Bloom's mapping, assignments with rubrics, and a "Before You Teach" checklist.
- **Student slides** (PPTX): native, editable PowerPoint with a diagram for every section,
  drawn icons, worked examples, knowledge checks with answer slides, transitions and animations.
- **Optional narrated video** (MP4): diagrams build up step by step while the lesson is explained,
  with captions, optional illustrations (Gemini), optional Manim animations for maths and
  science scenes, and optional short Veo motion clips for picture scenes.

**Research Paper Assistant.** From one uploaded manuscript (PDF or Word) it prepares:

- **Review report** (DOCX and PDF): a peer-review style report with a recommendation, a scored
  assessment on six to eight criteria, strengths, major and minor issues pinned to exact sections
  and paragraphs, a check of the central claims against the evidence, methodology, statistics and
  reproducibility checks, a reporting-guideline checklist (CONSORT, PRISMA, STROBE and others),
  research gaps, literature positioning against the professor's own research library (and,
  optionally, recent web sources), title and abstract advice, venue fit, a prioritised revision
  plan and questions for the authors.
- **Revised paper** (DOCX and PDF, plus a Word copy with tracked changes): a journal-style
  revision planned from the review. Sections are rewritten in a scholarly register or expanded,
  missing sections (related work, limitations, open challenges) are added, comparison tables are
  built, and recent literature is cited, but only references confirmed in Crossref or arXiv, in the
  paper's own citation style. Every original citation and number is kept exactly and no result is
  invented. Where a fix needs the author's own data, a highlighted `[Author: ...]` note marks the
  place.

Content is written in plain, natural language, and exported files carry no tool or AI markers in
their text or metadata.

## Documentation

- [Project brief](docs/PROJECT_BRIEF.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Code walkthrough](docs/CODE_WALKTHROUGH.md)
- [Agent workflows](docs/AGENTS.md)
- [Models and techniques](docs/MODELS_AND_AGENTS.md)
- [RAG design](docs/RAG_DESIGN.md)
- [API reference](docs/API.md)
- [Local setup](docs/SETUP.md)
- [Data model and audit](docs/DATA_MODEL_AND_AUDIT.md)
- [Security and compliance](docs/SECURITY_AND_COMPLIANCE.md)
- [AWS deployment](docs/AWS_DEPLOYMENT.md)
- [AWS deployment walkthrough](docs/AWS_DEPLOYMENT_WALKTHROUGH.md)
- [Operations runbook](docs/OPERATIONS_RUNBOOK.md)
- [Testing and QA](docs/TESTING_AND_QA.md)
- [Roadmap](docs/ROADMAP.md)
- [Owner handoff guide](docs/OWNER_HANDOFF_GUIDE.md)

## Architecture

```text
Browser
  │ email + OTP / HTTPS
  ▼
CloudFront -> shared ALB -> isolated ECS web service
  ├── SQS -> isolated ECS worker service
  ├── shared RDS instance / dedicated logical database and role
  ├── S3: tenant-isolated uploads, RAG sources, generated artifacts
  └── OpenAI Responses API: configured model + optional web search
```

The local profile uses SQLite and local disk. Production switches to PostgreSQL and S3 using
environment variables; no code change is required.

## Local setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Set OPENAI_API_KEY in .env. For reuse, copy the value from your existing local
# Multi-Rag-AI environment; do not commit it.
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`. In local mode, the OTP is returned by the request endpoint and shown
in the UI. Use `LLM_SERVICE_MODE=mock` to test without calling OpenAI.

## API summary

- `POST /api/auth/request-otp`, `POST /api/auth/verify`, `POST /api/auth/logout`
- `POST /api/documents`, `GET /api/documents`
- `POST /api/agents/teaching` — enqueue a lesson job
- `POST /api/agents/research` — enqueue a manuscript review (upload the paper first)
- `GET /api/jobs`, `GET /api/jobs/{job_id}`, `GET /api/artifacts/{id}`
- `GET /health`

Interactive API documentation is available at `/docs`.

## Audit and security controls

- OTPs and session tokens are stored as salted SHA-256 hashes, never plaintext.
- OTPs expire, are single-use, and stop verifying after five failed attempts.
- Cookies are HTTP-only, SameSite=Lax, and Secure in production.
- Every login, upload, successful agent run, and failed agent run creates an `audit_events` row.
- Agent requests are queued immediately, then completed asynchronously and tracked through
  `agent_jobs`.
- Every query is tenant-scoped; S3 keys use `tenants/{tenant_id}/...`.
- Uploads enforce extension and size allow-lists. S3 server-side encryption is enabled, with
  optional KMS.
- Jobs retain request/result/model/error metadata for operational traceability.
- `AGENT_EXECUTION_BACKEND=sqs` moves long agent runs to a durable worker queue. Run the same
  image with `SERVICE_MODE=worker`; the default `background` mode preserves local development.
- Secrets belong in AWS Secrets Manager/SSM and ECS task configuration, not Git.

## AWS production deployment

1. Create an S3 bucket with public access blocked, versioning, lifecycle rules, and encryption.
2. Create PostgreSQL in RDS (recommended for production). Set `DATABASE_URL` to the psycopg URL.
3. Give the application task roles least-privilege access to the configured S3 prefix, SQS queue,
   secrets, and KMS key.
4. Run the image as separate ECS web and worker services behind the shared ALB:

```text
ENVIRONMENT=production
SECRET_KEY=<strong-random-secret>
DATABASE_URL=postgresql+psycopg://...
OPENAI_API_KEY=<secret>
OPENAI_MODEL=gpt-5.5
STORAGE_PROVIDER=s3
S3_BUCKET=<bucket>
AWS_REGION=ap-south-1
AUTH_ENABLED=true
OTP_DEV_MODE=false
COOKIE_SECURE=true
AGENT_EXECUTION_BACKEND=sqs
AGENT_QUEUE_URL=<sqs-queue-url>
SMTP_HOST=<smtp-host>
SMTP_USERNAME=<smtp-user>
SMTP_PASSWORD=<smtp-secret>
SMTP_FROM=<verified-sender>
```

5. Keep TLS and the public domain on CloudFront; route to the application's ALB target group by
   the private origin header.
6. Deploy a new immutable image, update task definitions, wait for service stability, and run the
   smoke checks in the deployment walkthrough.

For a quick prototype without RDS, SQLite runs on one EC2 instance, but it is not safe for
autoscaling or instance replacement.

## Validation

```bash
ruff check .
pytest
```

## Current production behavior and boundaries

- Production agent execution uses SQS and a separate worker service: API calls enqueue work
  quickly, the UI polls job status, and completed teaching jobs expose JSON, DOCX, PDF, PPTX, and
  an optional content-grounded multimedia MP4. A separate structured storyboard expands the validated
  material into a mini-lecture with explanations, examples, misconceptions, and a recap. The
  default duration range is 4–15 minutes, and professors can override it per request.
- Local development keeps the `background` backend so developers do not need AWS services.
- Retrieval currently chunks up to 250 tenant documents, ranks excerpts with TF-IDF (with a lexical
  fallback), limits per-document dominance, and sends bounded source-labelled context.
  For large corpora, add OpenAI vector stores, pgvector, or Qdrant while retaining S3 as the
  source-of-record.
- Production OTP and first-time email verification use Amazon SES/SMTP configuration; development
  mode can display local OTPs.
- Scanned PDFs are supported when OCR is configured locally or through Amazon Textract; text PDFs
  continue to use native extraction.
- Model-created citations must still be reviewed by a professor before publication.
