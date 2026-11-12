# Project Brief

## Objective

Professor AI Workspace is a faculty productivity platform with two specialist agents:

- the **AI Teaching Assistant**, for lecture and assessment preparation;
- the **Research Paper Assistant**, which reviews an uploaded manuscript like a demanding journal
  reviewer and returns a review report and a revised, improved version of the paper.

The product reduces repetitive lesson preparation and speeds up the revise-and-resubmit cycle while
preserving professor review, source awareness, and institutional data isolation.

What sets the Research Paper Assistant apart from generic writing tools:

- every comment points to an exact section and paragraph of the manuscript;
- the central claims are checked against the evidence the paper actually presents;
- reporting guidelines (CONSORT, PRISMA, STROBE, COREQ and others) are checked for the paper type;
- the paper is positioned against the professor's own research library, and optionally against
  recent web sources;
- the revised paper keeps every citation and number exactly, never invents data, marks what only
  the author can add, and comes as a Word file with real tracked changes;
- the report ends with a prioritised revision plan and questions for the authors.

## Target Users

- University and college professors
- Business-school faculty
- School and college teachers preparing classroom sessions
- Academic departments and teaching-support teams
- Faculty development and curriculum teams

## Current Capabilities

Given a topic, course, audience, difficulty, and session length, the Teaching Assistant prepares:

- **Teacher's guide** (DOCX and PDF), the teacher's private copy:
  - lecture structure and timing
  - learning objectives with Bloom's levels
  - teaching notes and classroom activities
  - MCQs with answer keys, explanations, and the objective each one assesses
  - assignments with rubrics
  - numerical problems and worked solutions
  - Bloom's taxonomy mapping
  - case study with teaching notes
  - discussion questions and viva questions with model answers
  - a "Before You Teach" checklist of points to verify
- **Student slides** (PPTX): a native, editable deck with a diagram for every lesson part, drawn
  icons, worked examples, misconception cards, knowledge checks with answer slides, rubric bars,
  a recap, slide transitions and entrance animations.
- **Optional narrated video** (MP4): a mini-lecture where each diagram builds up step by step and
  scenes change with animated transitions.

Writing is plain and simple by design: short sentences, everyday words, every term defined.
Exported files carry neutral metadata and no tool or AI markers.

## Product Principles

- **Professor in control:** generated work is a draft for expert review; the teacher's guide lists
  points to check before teaching.
- **Grounded where possible:** uploaded course materials are attached as bounded context.
- **No fabricated references:** prompts explicitly prohibit invented citations.
- **Right material for the right reader:** answer keys and teaching notes stay in the teacher's
  guide; slides and video are written for students.
- **Private by institution:** database queries and object-storage paths are tenant-scoped.
- **Auditable:** login, upload, completion, and failure events are recorded.
- **Deployable:** local SQLite/disk can be replaced by PostgreSQL and S3 through configuration.

## Success Criteria

- A professor can sign in using email OTP.
- A professor can upload lecture notes, books, and case studies.
- The Teaching Assistant uses the professor's tenant-scoped course materials.
- Each lesson is downloadable as JSON, DOCX, PDF, and PPTX, plus MP4 when requested.
- Past lessons can be reopened and downloaded again from "My lessons".
- Agent jobs, artifacts, and security events are traceable.
- The same image supports local execution and isolated ECS web/worker services in AWS.

## Current Boundaries

- Agent execution uses job polling and a durable SQS/ECS worker in production, with a lightweight
  background fallback for local development.
- Retrieval is bounded lexical ranking rather than vector search.
- PDF extraction supports text PDFs and can use local OCR or Amazon Textract for scanned PDFs when
  OCR is configured.
- References and factual claims require professor review before classroom use.
- The narrated video states once that its voice is AI-generated, as the speech provider's terms
  require.
- Production email delivery and first-time email verification require SMTP or Amazon SES
  configuration.
