# Data Model and Audit

## Tables

Schema is declared in `app/core/models.py` and created at startup.

### `users`

Stores professor identity, tenant, role, active state, creation time, and last login.

### `otp_challenges`

Stores hashed OTP values, attempt count, expiry, consumption time, and creation time.

### `auth_sessions`

Stores hashed session tokens, user relationship, expiry, revocation, and creation time.

### `source_documents`

Stores:

- tenant and uploader
- collection and filename
- content type and size
- SHA-256 checksum
- local/S3 URI
- extracted text
- processing status

### `agent_jobs`

Stores:

- tenant and professor
- teaching or research agent type
- lifecycle status
- request and result JSON
- model name
- bounded failure message
- start and completion timestamps

### `artifacts`

Stores artifact type, filename, content type, storage URI, size, checksum, job relationship, and
creation time.

### `audit_events`

Stores actor, tenant, event type, entity, status, request ID, IP address, details, and timestamp.

## Current Audit Events

- `auth.otp_requested`
- `auth.login`
- `document.uploaded`
- `agent.teaching.queued`
- `agent.teaching.completed`
- `agent.teaching.failed`
- `agent.research.queued`
- `agent.research.completed`
- `agent.research.failed`

Queued events are emitted when the HTTP request creates a job. Completion and failure events are
emitted by the asynchronous background task.

## Useful PostgreSQL Queries

Recent activity:

```sql
select created_at, event_type, actor_email, status, entity_id, details
from audit_events
order by created_at desc
limit 50;
```

Failed jobs:

```sql
select id, tenant_id, created_by, agent_type, error_message, started_at, completed_at
from agent_jobs
where status = 'failed'
order by started_at desc;
```

Tenant artifact inventory:

```sql
select tenant_id, artifact_type, count(*) as total, sum(size_bytes) as bytes
from artifacts
group by tenant_id, artifact_type
order by tenant_id, artifact_type;
```

## Production Recommendations

- Use Alembic instead of startup `create_all`.
- Add OTP request rate-limit and delivery-result events.
- Add logout, session expiry, and artifact download events.
- Add document deletion and retention events.
- Add model prompt/version and token-usage fields.
- Add immutable log export to CloudWatch or a security archive.
- Avoid storing more model input/output than required by institutional policy.
