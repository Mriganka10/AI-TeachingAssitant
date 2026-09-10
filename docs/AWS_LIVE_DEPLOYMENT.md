# AWS Live Deployment Record

Status as of 10 September 2026: live at `https://professoraihub.com`; `/health` returns HTTP 200.

The active runtime is CloudFront -> shared ALB -> isolated Professor AI ECS web service, plus SQS -> isolated ECS worker. Metadata is stored in Professor AI's logical PostgreSQL database and role on the shared protected RDS instance; files remain in private tenant-scoped S3 storage. The domain and user-facing behavior were preserved.

Production uses `AGENT_EXECUTION_BACKEND=sqs` and `SERVICE_MODE=worker` for the worker. OTP is delivered through the configured production email provider; development OTP return is disabled and cookies are secure.

The previous Elastic Beanstalk environment is paused for a temporary 7–14 day rollback observation window. It is not an active dependency. Follow [AWS deployment](AWS_DEPLOYMENT.md), [operations](OPERATIONS_RUNBOOK.md), and [code walkthrough](CODE_WALKTHROUGH.md) for current procedures.
