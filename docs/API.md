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

- `collection`: `research_papers`, `previous_notes`, `books`, or `case_studies`
- `file`: PDF, DOCX, TXT, MD, or CSV

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/documents \
  -b cookies.txt \
  -F "collection=research_papers" \
  -F "file=@paper.pdf"
```

Maximum size defaults to 50 MB and is configured by `MAX_UPLOAD_MB`.

### List Sources

```http
GET /api/documents
```

Only documents for the signed-in tenant are returned.

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
  "collections": ["previous_notes", "books", "case_studies"],
  "instructions": "Include one Indian business example"
}
```

The response includes `job_id`, structured `result`, and downloadable artifact metadata.

## Research Paper Assistant

```http
POST /api/agents/research
Content-Type: application/json
```

```json
{
  "research_topic": "Responsible AI adoption in higher education",
  "discipline": "Information Systems",
  "use_web_search": true,
  "collections": ["research_papers"],
  "instructions": "Focus on longitudinal evidence"
}
```

## Jobs

```http
GET /api/jobs
```

Returns up to 50 recent tenant-scoped jobs with status, model, title, course/discipline, and start
time.

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
| 502 | OpenAI/model generation or output parsing failed. |
| 503 | Production OTP email service is not configured. |
