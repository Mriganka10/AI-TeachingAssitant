# Operations Runbook

## Daily Checks

- Elastic Beanstalk environment health is green.
- `/health` returns HTTP 200.
- OTP delivery succeeds.
- OpenAI requests do not show elevated 401, 429, or 5xx errors.
- RDS storage and connection counts are healthy.
- S3 upload and download errors are absent.
- Failed agent-job count is within expected limits.

## Useful Commands

Local:

```bash
python -m uvicorn app.main:app --reload
python -m pytest -q
python -m ruff check .
curl http://127.0.0.1:8000/health
```

Elastic Beanstalk:

```bash
eb status
eb health
eb events
eb logs
```

## Troubleshooting

### Application does not import

Correct entry point:

```text
app.main:app
```

### Startup rejects `SECRET_KEY`

Production refuses the development default. Generate and configure a long random value.

### Agent job fails

Inspect:

- `agent_jobs.error_message`
- `audit_events`
- application logs
- OpenAI key, quota, and model access
- whether model output was valid JSON

The browser should receive a queued `job_id` quickly. If users report an HTML/JSON parsing message,
check CloudFront/Nginx logs for timeouts or cached frontend assets, then verify the deployed
`app.js` asset version in `index.html`.

### Agent stays running

Inspect:

- whether the EB instance is CPU/memory constrained
- OpenAI latency or rate limits
- OCR/document extraction duration for large uploads
- job row `started_at` age
- application container logs

The current implementation uses in-process FastAPI background tasks. A container restart can
interrupt running work; high-concurrency production should use SQS and worker instances.

### Artifact cannot be downloaded

Check:

- artifact belongs to signed-in tenant
- S3 URI is correct
- EB EC2 role has `GetObject`
- object exists
- KMS decrypt permission exists

### Upload extraction fails

Confirm supported format and file size. Text PDFs are extracted natively. Scanned PDFs require OCR
configuration: local Tesseract for development or Amazon Textract for AWS production.

## Database Recovery

- Use RDS automated backups and snapshots.
- Test point-in-time recovery in a non-production environment.
- Do not repair production by deleting tables.
- Introduce Alembic before making schema changes after launch.

## Key Rotation

For OpenAI, SMTP, database, or application secrets:

1. create replacement secret
2. update EB environment or secret manager
3. restart/redeploy if needed
4. verify functionality
5. revoke old credential
6. review logs for misuse

Changing `SECRET_KEY` invalidates existing OTP/session hashes and signs users out.

## Incident Priorities

| Priority | Example |
| --- | --- |
| P1 | Cross-tenant data exposure, public S3 data, stolen credential. |
| P2 | Login unavailable, all agent runs failing, artifact loss. |
| P3 | One agent degraded, some file formats failing. |
| P4 | Cosmetic UI or documentation issue. |

For P1, disable affected access, rotate credentials, preserve evidence, and notify the owner and
institutional security contact immediately.
