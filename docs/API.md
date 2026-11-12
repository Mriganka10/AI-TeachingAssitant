# API Reference

Local base URL:

```text
http://127.0.0.1:8000
```

Interactive OpenAPI documentation:

```text
http://127.0.0.1:8000/docs
```

## Health

```http
GET /health
```

```json
{"status":"ok","service":"Professor AI Workspace"}
```

## Authentication

### Request OTP

```http
POST /api/auth/request-otp
Content-Type: application/json
```

```json
{"email":"professor@university.edu"}
```

Local non-production mode may return `dev_otp`. Production must deliver OTP through SMTP and must
not return the code.

### Verify OTP

```http
POST /api/auth/verify
Content-Type: application/json
```

```json
{"email":"professor@university.edu","otp":"123456"}
```

The response sets an HTTP-only session cookie.

### Current User

```http
GET /api/auth/me
```

### Logout

```http
POST /api/auth/logout
```

## Documents

### Upload Source

```http
POST /api/documents
Content-Type: multipart/form-data
```

Fields:

- `collection`: `previous_notes` (lecture notes), `books` or `case_studies` (read by the Teaching
  Assistant); `research_papers` (related literature for paper reviews); or `manuscripts` (papers to
  review).
- `file`: PDF, DOCX, TXT, MD, or CSV

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/documents \
  -b cookies.txt \
  -F "collection=previous_notes" \
  -F "file=@week3-notes.pdf"
```

Maximum size defaults to 50 MB and is configured by `MAX_UPLOAD_MB`.

### List Sources

```http
GET /api/documents
```

Only documents for the signed-in tenant are returned. Each item has `id`, `filename`,
`collection`, `size_bytes`, `status` and `uploaded_at`.

## Teaching Assistant

```http
POST /api/agents/teaching
Content-Type: application/json
```

```json
{
  "topic": "Time value of money",
  "course": "MBA Finance",
  "audience": "Postgraduate students",
  "duration_minutes": 60,
  "difficulty": "intermediate",
  "use_web_search": true,
  "generate_video": true,
  "video_min_minutes": 4,
  "video_max_minutes": 15,
  "collections": ["previous_notes", "books", "case_studies"],
  "instructions": "Include one Indian business example"
}
```

The response queues the job and returns immediately:

```json
{
  "job_id": "uuid",
  "status": "running",
  "result": null,
  "error": null,
  "artifacts": []
}
```

Use `GET /api/jobs/{job_id}` to poll until the job reaches `completed` or `failed`.
`video_min_minutes` and `video_max_minutes` are optional positive integers. Their deployment
defaults remain 4 and 15, but a request may select a shorter or longer range. Both values must be
provided together, and the minimum cannot exceed the maximum.

When `generate_video` is true, a successful teaching job includes an `mp4` artifact containing a
content-grounded multimedia overview with several explanatory visual compositions and
provider-selectable AI narration. The final duration is verified against the requested range. If it
fails, the job remains completed with its JSON, DOCX, PDF, and PPTX artifacts and returns a
non-fatal `warning`.

## Research Paper Assistant

```http
POST /api/agents/research
Content-Type: application/json
```

```json
{
  "manuscript_document_id": "id returned by POST /api/documents",
  "discipline": "Educational psychology",
  "paper_type": "auto",
  "target_venue": "Computers & Education",
  "author_name": "Dr A. Sen",
  "generate_revision": true,
  "use_web_search": false,
  "collections": ["research_papers"],
  "instructions": "Is the methodology strong enough for a Q1 journal?"
}
```

- `manuscript_document_id` (required): an uploaded document of the same tenant with readable
  text. Unknown IDs return 404; documents without text return 422.
- `paper_type`: `auto`, `empirical`, `review`, `theoretical`, `methods`, `case_study` or `other`.
- `author_name`: written into the document properties of the exported files and used as the
  author of the tracked changes. Leave blank for blank metadata.
- `generate_revision`: when false, only the review report is produced.
- `collections`: where related literature is retrieved from; the manuscript itself is excluded.

A completed review job has these artifacts: `<title>-review.json`, `<title>-review.docx`,
`<title>-review.pdf` and, with a revision, `<title>-revised.docx`, `<title>-revised.pdf` and
`<title>-revised-tracked-changes.docx`. `result` holds the review, the request summary, the parsed
manuscript (`manuscript`) and the revision: `revision.sections[]` (with `heading`,
`original_heading`, `level`, `action` keep/revise/expand/add, `status`, `original`, `revised`,
`tables`, `changes` and `author_notes`), `revision.keywords`, `revision.new_references` (verified,
with DOI or arXiv ID) and `revision.dropped_sections`. Set `use_web_search` to true to allow
verified new references. If some sections had to keep their original text, the job
is `completed` with a `warning` naming them.

## Jobs

```http
GET /api/jobs
```

Returns up to 50 recent tenant-scoped jobs (lessons and paper reviews) with `agent_type`, status,
model, title (the lesson topic, or the manuscript title once reviewed), course or target venue,
and start time. `GET /api/jobs/{job_id}` also returns `agent_type`.

### Job Detail / Polling

```http
GET /api/jobs/{job_id}
```

Queued or running job:

```json
{
  "job_id": "uuid",
  "status": "running",
  "result": null,
  "error": null,
  "artifacts": []
}
```

Completed job:

```json
{
  "job_id": "uuid",
  "status": "completed",
  "request": {"topic": "Time value of money", "duration_minutes": 60, "generate_video": false},
  "started_at": "2026-10-31T02:30:00Z",
  "completed_at": "2026-10-31T02:33:10Z",
  "result": {"title": "Time value of money"},
  "error": null,
  "warning": null,
  "artifacts": [
    {"id": "uuid", "filename": "time-value-of-money.json", "type": "json", "size_bytes": 18234},
    {"id": "uuid", "filename": "time-value-of-money.docx", "type": "docx", "size_bytes": 40211},
    {"id": "uuid", "filename": "time-value-of-money.pdf", "type": "pdf", "size_bytes": 51877},
    {"id": "uuid", "filename": "time-value-of-money.pptx", "type": "pptx", "size_bytes": 102400}
  ]
}
```

`request` is the original lesson request (the UI uses it to show whether a video was asked for).
Status moves through `running` → `content_ready` → (`documents_ready` when a video was requested)
→ `completed`, or ends in `failed` / `artifact_failed`. DOCX and PDF are the teacher's guide; PPTX
and MP4 are for students.

Failed job:

```json
{
  "job_id": "uuid",
  "status": "failed",
  "result": null,
  "error": "bounded failure message",
  "artifacts": []
}
```

## Artifact Download

```http
GET /api/artifacts/{artifact_id}
```

The artifact must belong to the signed-in tenant. Cross-tenant access returns 404.

## Error Behavior

| Status | Meaning |
| --- | --- |
| 401 | Sign-in required, invalid OTP, or expired session. |
| 404 | Tenant-scoped artifact not found. |
| 413 | Upload exceeds configured size. |
| 415 | Unsupported document format. |
| 422 | Request validation error. |
| 502 | Unexpected API-level failure before a job is queued. |
| 503 | Production OTP email service is not configured. |

OpenAI/model generation and artifact errors are normally stored on the async job as `status:
failed` with a bounded `error` field instead of holding the original HTTP request open.
