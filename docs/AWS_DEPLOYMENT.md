# AWS Deployment Guide

## Target

```text
Elastic Beanstalk -> EC2 -> FastAPI
                         +-> RDS PostgreSQL
                         +-> private S3
                         +-> OpenAI API
                         +-> SMTP / Amazon SES
```

Recommended region for this project is `ap-south-1`, matching the application defaults. Choose a
different region only after updating resource configuration consistently.

## Required AWS Resources

- Elastic Beanstalk application and environment
- EC2 instance profile for Elastic Beanstalk
- Private S3 bucket
- RDS PostgreSQL instance
- Security groups connecting EB EC2 to RDS
- SMTP provider or Amazon SES SMTP credentials
- CloudWatch logs and alarms
- ACM certificate and load balancer for HTTPS

## Production Environment Variables

```text
APP_NAME=Professor AI Workspace
ENVIRONMENT=production
SECRET_KEY=<long-random-application-secret>
DATABASE_URL=postgresql+psycopg://user:password@host:5432/professor_ai
OPENAI_API_KEY=<openai-key>
OPENAI_MODEL=gpt-5.5
OPENAI_REASONING_EFFORT=medium
LLM_SERVICE_MODE=openai
AUTH_ENABLED=true
OTP_DEV_MODE=false
OTP_TTL_MINUTES=10
SESSION_TTL_MINUTES=720
COOKIE_SECURE=true
SMTP_HOST=<smtp-host>
SMTP_PORT=587
SMTP_USERNAME=<smtp-user>
SMTP_PASSWORD=<smtp-password>
SMTP_FROM=<verified-sender>
STORAGE_PROVIDER=s3
S3_BUCKET=<private-bucket>
S3_PREFIX=professor-ai
S3_KMS_KEY_ID=<optional-kms-key>
AWS_REGION=ap-south-1
MAX_UPLOAD_MB=50
MAX_CONTEXT_CHARS=120000
```

## S3 Layout

```text
professor-ai/
  tenants/
    {tenant_id}/
      rag/{document_id}/{filename}
      artifacts/{artifact_id}/{filename}
```

Enable:

- block public access
- bucket versioning
- default encryption
- lifecycle expiration where institutionally appropriate
- access logging or CloudTrail data events for higher-assurance deployments

## IAM

The EB EC2 role needs only:

- `s3:GetObject`
- `s3:PutObject`
- optionally `s3:DeleteObject`
- `kms:Encrypt`, `kms:Decrypt`, and `kms:GenerateDataKey` when using KMS

Scope permissions to the application bucket and prefix.

## RDS

Use PostgreSQL and a URL in SQLAlchemy psycopg form:

```text
postgresql+psycopg://user:password@host:5432/professor_ai
```

RDS should:

- be private
- accept port 5432 only from the EB EC2 security group
- use encryption at rest
- have automated backups
- use a secret-managed password

## Elastic Beanstalk

The repository includes:

- `Procfile`
- `Dockerfile`
- `.ebextensions/01_options.config`

Example EB CLI flow:

```bash
eb init professor-ai --platform python --region ap-south-1
eb create professor-ai-prod
eb setenv ENVIRONMENT=production AUTH_ENABLED=true OTP_DEV_MODE=false \
  COOKIE_SECURE=true STORAGE_PROVIDER=s3
eb deploy
eb status
eb open
```

Set secret values separately and avoid shell history exposure.

## HTTPS

For production:

1. create or validate an ACM certificate
2. use a load-balanced EB environment
3. attach the certificate to the HTTPS listener
4. redirect HTTP to HTTPS
5. set `COOKIE_SECURE=true`

Do not enable secure cookies on a plain HTTP-only environment during initial testing; browsers will
not return them.

## Health and Verification

```bash
curl https://<domain>/health
```

Then verify:

1. OTP email delivery
2. session cookie behavior
3. document upload to S3
4. document metadata in RDS
5. both agent runs
6. artifact download after instance replacement
7. audit-event rows
8. CloudWatch application logs

## Production Limitations

- SQLite must not be used across multiple instances.
- Local EC2 disk is temporary and must not be the source of record.
- Synchronous agent calls can exceed comfortable web-request duration.
- Startup schema creation is not a substitute for migrations.
- Introduce a queue/worker design before heavy or multi-user usage.
