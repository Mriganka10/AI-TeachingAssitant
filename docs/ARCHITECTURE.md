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
       +-- Teaching Agent API
       +-- Research Agent API
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
  -> create running agent_job
  -> retrieve tenant-scoped source excerpts
  -> build controlled model payload
  -> call OpenAI or deterministic mock
  -> parse structured JSON
  -> generate artifacts
  -> persist artifact metadata and files
  -> mark job completed
  -> record audit event
```

Failures mark the job as `failed`, store a bounded error message, and create a failed audit event.

## Local Architecture

```text
Uvicorn/FastAPI
  +-- SQLite: data/professor_ai.db
  +-- Local files: data/professor-ai/tenants/...
  +-- OpenAI API or LLM_SERVICE_MODE=mock
  +-- Local Tesseract OCR for scanned PDFs
```

## AWS Target Architecture

```text
HTTPS / Application Load Balancer
            |
            v
Elastic Beanstalk
            |
            v
EC2 instances running FastAPI/Uvicorn
   |             |                 |
   v             v                 v
RDS PostgreSQL   Private S3        OpenAI API / Textract
metadata/audit   sources/artifacts model + web search / OCR
```

Elastic Beanstalk manages application deployment and EC2 health. EC2 runs the application.
RDS is the durable metadata/audit store. S3 is the durable file source of record.

## Scaling Boundary

The current request thread performs model calls and document generation synchronously. Before
autoscaling or enabling large research corpora, introduce:

- SQS job queue
- background workers on ECS, EC2, or Elastic Beanstalk worker tier
- idempotency keys and retry policies
- vector retrieval using OpenAI vector stores, pgvector, or Qdrant

Schema changes are managed through Alembic. Deployments run `alembic upgrade head` before starting
the web process.
