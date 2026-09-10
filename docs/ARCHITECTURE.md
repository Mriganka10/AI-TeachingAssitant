# Architecture

## High-Level Design

```text
Professor Browser
       |
       v
FastAPI Web Application
       |
       +-- Email OTP Authentication
       +-- Faculty Dashboard
       +-- Document Upload API
       +-- Teaching Agent API -> async job polling
       +-- Research Agent API -> async job polling
       |
       v
Tenant-Scoped Application Services
       |
       +-- SQLAlchemy persistence
       +-- Source document extraction
       +-- Bounded multi-collection retrieval
       +-- OpenAI Responses API + optional web search
       +-- Structured PPTX / DOCX / PDF / JSON generation
       +-- Local disk or encrypted S3 storage
       +-- Audit-event recording
```

## Runtime Components

| Component | File | Responsibility |
| --- | --- | --- |
| Application entry point | `app/main.py` | Starts FastAPI, initializes schema, serves UI and health endpoint. |
| API routes | `app/api/routes.py` | Authentication, upload, agent execution, jobs, and downloads. |
| Queue publisher | `app/core/job_queue.py` | Publishes durable job identifiers to SQS in production. |
| Worker | `app/worker.py` | Consumes SQS messages and completes idempotent agent jobs. |
| Configuration | `app/core/config.py` | Environment-driven local and production settings. |
| Database | `app/core/database.py` | SQLAlchemy engine, sessions, and schema initialization. |
| Data models | `app/core/models.py` | Users, OTPs, sessions, documents, jobs, artifacts, and audit events. |
| Authentication | `app/core/auth.py` | OTP creation, verification, sessions, cookies, SMTP delivery. |
| Retrieval | `app/agents/retrieval.py` | Tenant and collection filtering plus lexical ranking. |
| LLM service | `app/agents/llm.py` | OpenAI Responses API, optional web search, mock mode, JSON parsing. |
| Agent prompts | `app/agents/prompts.py` | Teaching and research output contracts. |
| Artifact generator | `app/artifacts/generator.py` | Professor-ready JSON, DOCX, PDF, and PPTX creation with headings, tables, assessment sections, evidence callouts, and references. |
| Storage | `app/core/storage.py` | Local persistence or encrypted S3 upload/download. |
| Faculty UI | `app/static/` | Login, dashboard, agent workbenches, library, and output viewer. |

## Request Flow

### Sign-In

```text
Email -> OTP challenge -> hashed OTP in database -> verify -> hashed session -> HTTP-only cookie
```

### Document Ingestion

```text
Upload
  -> extension and size validation
  -> temporary file
  -> native text extraction
  -> OCR fallback for scanned PDFs (Tesseract locally or Amazon Textract)
  -> SHA-256 checksum
  -> source_documents row
  -> local/S3 persistence
  -> audit event
```

Supported source formats are PDF, DOCX, TXT, MD, and CSV.

### Agent Execution

```text
Faculty request
  -> create running agent_job and return job_id
  -> UI polls GET /api/jobs/{job_id}
  -> web service publishes job_id to SQS
  -> independent worker reloads the job and retrieves tenant-scoped source excerpts
  -> build controlled model payload
  -> call OpenAI or deterministic mock
  -> parse structured JSON
  -> generate artifacts
  -> persist artifact metadata and files
  -> mark job completed
  -> record audit event
```

Failures mark the job as `failed`, store a bounded error message, and create a failed audit event.
The original browser request is not held open for the full model/document-generation duration,
which prevents CloudFront or Nginx timeout pages from being parsed as JSON by the UI.

## Local Architecture

```text
Uvicorn/FastAPI
  +-- SQLite: data/professor_ai.db
  +-- Local files: data/professor-ai/tenants/...
  +-- OpenAI API or LLM_SERVICE_MODE=mock
  +-- Local Tesseract OCR for scanned PDFs
```

## Current AWS Production Architecture

```text
professoraihub.com / CloudFront
              |
              v
shared Application Load Balancer -- private application routing header
              |
              v
isolated ECS web service -> SQS queue -> isolated ECS worker service
       |                         |
       +---- shared RDS instance, dedicated database/role
       +---- private S3 prefixes, Secrets Manager/SSM, OpenAI/Textract
```

The four applications share only the load balancer, ECS capacity, and physical RDS instance.
Professor AI retains separate services, target group, task roles, secrets, queue/DLQ, database,
database role, and storage namespace. This reduces idle cost without coupling application data or
release lifecycles. The legacy Elastic Beanstalk environment is paused during the rollback window.

## Scaling Boundary

The durable SQS/worker boundary is implemented. ECS can scale web and worker task counts
independently as traffic grows, while queue depth provides back-pressure. Message redelivery is
safe at the completed-job boundary and repeated failures go to a DLQ. The next scaling boundary is
retrieval: large corpora should use OpenAI vector stores, pgvector, or Qdrant.

Schema changes are managed through Alembic. Deployments run `alembic upgrade head` before starting
the web process.
