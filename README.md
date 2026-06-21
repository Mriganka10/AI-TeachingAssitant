# Professor AI Workspace

A deployable, multi-tenant baseline for two professor-facing agents:

1. **AI Teaching Assistant** — creates lecture structure, PPTX, notes, MCQs, assignments,
   numerical problems, Bloom's taxonomy mapping, case discussions, and viva questions.
2. **Research Paper Assistant** — synthesizes uploaded papers into a literature review,
   methodology comparison, research gaps, research questions, future scope, methodology
   suggestions, and APA-style references.

## Documentation

- [Project brief](docs/PROJECT_BRIEF.md)
- [Architecture](docs/ARCHITECTURE.md)
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
Elastic Beanstalk (nginx + FastAPI)
  ├── EC2 application instances
  ├── PostgreSQL/RDS: users, OTPs, sessions, jobs, documents, artifacts, audit events
  ├── S3: tenant-isolated uploads, RAG sources, generated PPTX/PDF/DOCX/JSON
  └── OpenAI Responses API: GPT-5.5 + optional web search
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
- `POST /api/agents/teaching`, `POST /api/agents/research`
- `GET /api/jobs`, `GET /api/artifacts/{id}`
- `GET /health`

Interactive API documentation is available at `/docs`.

## Audit and security controls

- OTPs and session tokens are stored as salted SHA-256 hashes, never plaintext.
- OTPs expire, are single-use, and stop verifying after five failed attempts.
- Cookies are HTTP-only, SameSite=Lax, and Secure in production.
- Every login, upload, successful agent run, and failed agent run creates an `audit_events` row.
- Every query is tenant-scoped; S3 keys use `tenants/{tenant_id}/...`.
- Uploads enforce extension and size allow-lists. S3 server-side encryption is enabled, with
  optional KMS.
- Jobs retain request/result/model/error metadata for operational traceability.
- Secrets belong in Elastic Beanstalk environment properties or AWS Secrets Manager, not Git.

## AWS deployment (Elastic Beanstalk → EC2 → S3)

1. Create an S3 bucket with public access blocked, versioning, lifecycle rules, and encryption.
2. Create PostgreSQL in RDS (recommended for production). Set `DATABASE_URL` to the psycopg URL.
3. Give the EB instance profile least-privilege access to the configured S3 prefix and KMS key.
4. Create an EB Python or Docker environment and configure:

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
SMTP_HOST=<smtp-host>
SMTP_USERNAME=<smtp-user>
SMTP_PASSWORD=<smtp-secret>
SMTP_FROM=<verified-sender>
```

5. Configure an HTTPS ACM certificate on the load balancer and redirect HTTP to HTTPS.
6. Deploy with `eb init`, `eb create`, and `eb deploy`, or upload a source bundle.

For a quick prototype without RDS, SQLite runs on one EC2 instance, but it is not safe for
autoscaling or instance replacement.

## Validation

```bash
ruff check .
pytest
```

## Current production boundaries

- Agent execution is synchronous. Add SQS/Celery workers before enabling long-running jobs at
  high concurrency.
- Retrieval currently ranks up to 250 tenant documents lexically and sends bounded excerpts.
  For large corpora, add OpenAI vector stores, pgvector, or Qdrant while retaining S3 as the
  source-of-record.
- SMTP is required when `ENVIRONMENT=production` and development OTP mode is disabled.
- Model-created citations must still be reviewed by a professor before publication.
