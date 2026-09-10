# AWS Production Deployment

Last updated: 10 September 2026.

## Live topology

`professoraihub.com` remains the public URL. CloudFront terminates HTTPS and forwards to the shared Application Load Balancer. A private origin header selects Professor AI's target group. Separate ECS services run the web and SQS worker processes on the shared ARM ECS capacity pool.

The application shares the physical ALB, ECS capacity, and RDS instance with the other products, but owns its target group, ECS services/task roles, secrets, queue/DLQ, logical PostgreSQL database and role, and S3 namespace. This is infrastructure consolidation, not cross-application data sharing.

## Required runtime configuration

```text
ENVIRONMENT=production
DATABASE_URL=postgresql+psycopg://<app-role>:<secret>@<shared-rds>/<app-db>
STORAGE_PROVIDER=s3
S3_BUCKET=<private-bucket>
AWS_REGION=ap-south-1
AGENT_EXECUTION_BACKEND=sqs
AGENT_QUEUE_URL=<queue-url>
AGENT_QUEUE_WAIT_SECONDS=20
AGENT_QUEUE_VISIBILITY_SECONDS=900
AUTH_ENABLED=true
OTP_DEV_MODE=false
COOKIE_SECURE=true
```

Configure model, SMTP/SES, OCR, session, and encryption settings through Secrets Manager/SSM and the task definition. Never store secret values in Git or documentation.

The task role requires `ses:GetEmailIdentity`, `ses:CreateEmailIdentity`, `ses:SendEmail`, and
`ses:SendRawEmail` for first-time verification and OTP delivery. Missing identity permissions
causes registration HTTP 503 even when ordinary SES sending works.

## Image, data, and release

Build one immutable ARM-compatible image. The web task uses the default command; the worker task sets `SERVICE_MODE=worker`. Deploy the same image digest to both. The web receives ALB traffic; the worker has no public listener. Scale web tasks from request/CPU pressure and workers from queue depth or oldest-message age. Repeated failures go to the DLQ.

The application connects only with its own database role. RDS has seven-day automated backups and deletion protection. S3 remains private, encrypted, and tenant-prefixed.

1. Run lint/tests; record the source commit and image digest.
2. Back up the database and verify queue/DLQ state.
3. Register new worker and web task-definition revisions.
4. Apply backward-compatible migrations once.
5. Update worker, then web; wait for ECS stability.
6. Verify `/health`, OTP login, upload, both agents, polling, and downloads.
7. Watch ALB 5xx, task restarts, queue age/DLQ, RDS, and logs.

Rollback uses preceding task definitions and, only if schema compatibility requires it, the database restore plan. The former Elastic Beanstalk environment is paused—not active—and should remain only for the agreed 7–14 day rollback window.

See [deployment walkthrough](AWS_DEPLOYMENT_WALKTHROUGH.md), [operations](OPERATIONS_RUNBOOK.md), and [code walkthrough](CODE_WALKTHROUGH.md).
