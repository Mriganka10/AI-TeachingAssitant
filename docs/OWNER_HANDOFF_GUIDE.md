# Owner Handoff Guide

## Purpose

This guide helps the owner review, demonstrate, deploy, and assign future work for Professor AI.

## Recommended Reading Order

1. `README.md` — quick overview and commands
2. `docs/PROJECT_BRIEF.md` — product purpose and boundaries
3. `docs/ARCHITECTURE.md` — technical design
4. `docs/AGENTS.md` — Teaching and Research workflows
5. `docs/MODELS_AND_AGENTS.md` — OpenAI and deterministic techniques
6. `docs/RAG_DESIGN.md` — knowledge-library behavior and future vector design
7. `docs/API.md` — endpoint reference
8. `docs/SETUP.md` — local developer setup
9. `docs/DATA_MODEL_AND_AUDIT.md` — database and traceability
10. `docs/SECURITY_AND_COMPLIANCE.md` — privacy and academic-integrity controls
11. `docs/AWS_DEPLOYMENT.md` — production architecture and deployment
12. `docs/AWS_DEPLOYMENT_WALKTHROUGH.md` — service-by-service explanation
13. `docs/OPERATIONS_RUNBOOK.md` — support and incidents
14. `docs/TESTING_AND_QA.md` — release validation
15. `docs/ROADMAP.md` — next phases

## Technical-Team Summary

Use this explanation:

> Professor AI is a FastAPI application with email OTP login and two explicit specialist
> assistants. A professor uploads tenant-scoped academic sources, selects Teaching or Research,
> and submits a controlled request. The application queues a tenant-scoped agent job, retrieves
> bounded excerpts from the professor's library, optionally uses OpenAI web search, requires
> structured JSON output, generates academic artifacts, stores metadata and audit events in SQL,
> and persists files locally or in encrypted S3.

## Local Demonstration

```bash
git checkout release_branch
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
python -m uvicorn app.main:app --reload
```

For a no-cost demo:

```text
LLM_SERVICE_MODE=mock
```

Open `http://127.0.0.1:8000`.

## Business Demo Script

1. Show the professional professor login.
2. Request the local OTP.
3. Show the faculty dashboard and two assistants.
4. Upload a previous note or research paper.
5. Generate a lecture package.
6. Point out the queued/running job status while the agent works.
7. Download JSON, DOCX, PDF, and PPTX.
8. Generate a research synthesis.
9. Show research gaps and methodology comparison.
10. Download JSON, DOCX, PDF, and PPTX.
11. Show recent activity and source counts.
12. Explain tenant isolation, audit tables, S3, and AWS deployment.
13. State clearly that professors review all generated material.

## Owner Decisions Needed Before Production

- target institution and allowed email domains
- AWS account, region, and resource names
- SMTP/SES sender identity
- custom domain
- RDS sizing and backup retention
- S3 retention and deletion rules
- data-processing and copyright policy
- approved OpenAI model and budget
- whether current web search is allowed
- research-paper license and privacy constraints
- whether to keep in-process async jobs or move to SQS workers for scale

## Current Production Gaps

- in-process async execution should move to durable SQS workers before high-concurrency use
- lexical rather than vector retrieval
- no citation verification
- OCR requires local Tesseract or Amazon Textract configuration and operational validation
- no CSRF or OTP rate limiting
- no Alembic migrations
- no institutional SSO
- no professor approval/version workflow

## First Production Sprint

1. Rotate and centralize all secrets.
2. Create dev/stage/prod environments.
3. Add Alembic.
4. Move async jobs to SQS-backed workers.
5. Add vector retrieval and citation provenance.
6. Add CSRF, rate limiting, and domain allow-list.
7. Run security, privacy, and academic-integrity review.
