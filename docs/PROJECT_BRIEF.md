# Project Brief

## Objective

Professor AI Workspace is a faculty productivity platform with two specialist assistants:

1. **AI Teaching Assistant** for lecture and assessment preparation.
2. **Research Paper Assistant** for literature synthesis and research planning.

The product is designed to reduce repetitive academic preparation while preserving professor
review, source awareness, and institutional data isolation.

## Target Users

- University and college professors
- Business-school faculty
- Research supervisors and principal investigators
- Academic departments and teaching-support teams
- Faculty development and curriculum teams

## Current Capabilities

### Teaching Assistant

Given a topic, course, audience, difficulty, and session duration, the agent can produce:

- lecture structure and timing
- learning objectives
- PowerPoint deck
- lecture notes in DOCX and PDF
- MCQs with answers and explanations
- assignments and rubrics
- numerical problems and solutions
- Bloom's taxonomy mapping
- case-study prompts
- discussion and viva questions

### Research Paper Assistant

Given a research topic and uploaded papers, the agent can produce:

- executive literature synthesis
- thematic analysis
- methodology comparison
- research gaps
- research questions
- future scope
- methodology suggestions
- APA-style reference list
- presentation-ready PPTX summary

## Product Principles

- **Professor in control:** generated work is a draft for expert review.
- **Grounded where possible:** uploaded academic sources are attached as bounded context.
- **No fabricated references:** prompts explicitly prohibit invented citations.
- **Private by institution:** database queries and object-storage paths are tenant-scoped.
- **Auditable:** login, upload, completion, and failure events are recorded.
- **Deployable:** local SQLite/disk can be replaced by PostgreSQL and S3 through configuration.

## Success Criteria

- A professor can sign in using email OTP.
- A professor can upload trusted notes, books, cases, and research papers.
- Both assistants can use the professor's tenant-scoped source library.
- Teaching output is downloadable as JSON, DOCX, PDF, and PPTX.
- Research output is downloadable as JSON, DOCX, PDF, and PPTX.
- Agent jobs, artifacts, and security events are traceable.
- The same code runs locally and on AWS Elastic Beanstalk.

## Current Boundaries

- Agent execution uses an in-app asynchronous job flow with job polling. Durable queue workers
  such as SQS/Celery/RQ are still recommended before high-concurrency production scale.
- Retrieval is bounded lexical ranking rather than vector search.
- PDF extraction supports text PDFs and can use local OCR or Amazon Textract for scanned PDFs when
  OCR is configured.
- References and factual claims require professor review before academic use.
- Production email delivery and first-time email verification require SMTP or Amazon SES
  configuration.
