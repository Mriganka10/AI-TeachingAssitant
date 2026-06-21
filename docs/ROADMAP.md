# Roadmap

## Phase 1: Current Prototype

Status: implemented.

- Professional professor login and dashboard
- Email OTP and session authentication
- Teaching and Research assistants
- Uploaded academic-source library
- Optional OpenAI web search
- JSON, DOCX, PDF, and PPTX artifacts
- SQLite/PostgreSQL persistence
- local/S3 storage
- audit tables
- Docker and Elastic Beanstalk foundations
- tests and linting

## Phase 2: Retrieval Quality

- semantic chunking
- embeddings and vector search
- hybrid lexical/vector retrieval
- source reranking
- page-level citations
- DOI and URL validation
- scanned-document OCR

## Phase 3: Academic Quality Controls

- structured-output schema enforcement
- citation verifier
- Bloom taxonomy validation
- assessment difficulty and duplication checks
- accessibility checks
- discipline-specific templates
- prompt/model version tracking
- faculty feedback and regeneration controls

## Phase 4: Background Processing

- SQS job queue
- worker service
- progress and cancellation
- retries and idempotency
- notification when work completes
- large-batch paper ingestion

## Phase 5: Institutional Product

- departments, courses, and research projects
- Cognito or institutional SSO
- professor, reviewer, and administrator roles
- source-sharing permissions
- curated institutional knowledge bases
- retention and deletion workflows
- usage and cost dashboards

## Phase 6: Collaboration

- lecture-package versioning
- professor comments and approval
- co-author research workspace
- export templates with university branding
- assignment and rubric library
- LMS integration

## Phase 7: Production Hardening

- Alembic migrations
- CSRF and rate limiting
- malware scanning
- WAF and abuse controls
- CloudWatch dashboards and alarms
- disaster-recovery exercise
- penetration test
- privacy and academic-integrity review

## Immediate Next Sprint

1. Add vector-backed, chunk-level retrieval.
2. Enforce output schemas at model-call level.
3. Add citation verification and visible provenance.
4. Add SQS-based asynchronous jobs.
5. Add Alembic migrations.
6. Add OTP rate limiting and CSRF protection.
7. Add professor-controlled deletion and retention.
