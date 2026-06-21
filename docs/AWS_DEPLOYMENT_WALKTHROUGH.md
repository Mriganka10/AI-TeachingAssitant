# AWS Deployment Walkthrough

This document explains the AWS design in practical terms.

## What Each Service Does

### Elastic Beanstalk

Elastic Beanstalk receives the application bundle, creates or updates the environment, configures
the reverse proxy, starts the application process, and monitors environment health.

### EC2

EC2 is the virtual machine on which Uvicorn and FastAPI run. In this architecture, treat EC2 as a
Beanstalk-managed runtime rather than a server edited manually.

### RDS PostgreSQL

RDS stores durable structured information:

- professors and sessions
- source-document metadata and extracted text
- agent requests and results
- artifact metadata
- audit events

### S3

S3 stores durable files:

- uploaded papers, notes, books, and cases
- generated JSON, DOCX, PDF, and PPTX artifacts

### OpenAI

The application calls the OpenAI Responses API to generate structured teaching and research
content. The optional web-search tool is enabled per professor request.

### SMTP or Amazon SES

SMTP delivers the one-time login code. Local development can display an OTP, but production cannot.

## Deployment Sequence

1. **Prepare the account**
   - choose region
   - configure AWS CLI and EB CLI
   - confirm the active account

2. **Create S3**
   - block public access
   - enable encryption and versioning
   - add lifecycle policy

3. **Create RDS**
   - PostgreSQL
   - private subnet where possible
   - encrypted storage
   - backups enabled

4. **Create security-group rule**
   - allow PostgreSQL only from the EB EC2 security group

5. **Create EB environment**
   - Python or Docker platform
   - load balanced for production HTTPS

6. **Configure IAM**
   - attach least-privilege S3/KMS policy to EB EC2 role

7. **Configure environment**
   - set application settings
   - inject secrets
   - set database and bucket

8. **Deploy**
   - deploy source bundle
   - check EB events and health

9. **Verify**
   - health endpoint
   - OTP
   - upload
   - both agents
   - artifact persistence
   - RDS and S3 evidence

10. **Add domain and HTTPS**
    - ACM certificate
    - load-balancer listener
    - DNS record
    - secure cookies

## Common Problems

### EB health is red after deployment

Check:

```bash
eb logs
eb events
```

Confirm `Procfile` uses `app.main:app` and that environment variables are present.

### Database connection times out

Check RDS endpoint, security groups, port 5432, database name, credentials, and URL format.

### Upload succeeds but disappears after redeployment

The application is using local storage. Set `STORAGE_PROVIDER=s3` and `S3_BUCKET`.

### OTP cookie is not retained

If using plain HTTP, set `COOKIE_SECURE=false` temporarily. For production, configure HTTPS and set
it to `true`.

### S3 returns AccessDenied

Check the EB EC2 instance role, bucket name, prefix, KMS permissions, and bucket policy.

### OpenAI returns 401

Rotate the key and update the EB environment. A key stored in an old repository or screenshot may
already be invalid or revoked.

## Go-Live Checklist

- [ ] HTTPS is active.
- [ ] Secure cookies are enabled.
- [ ] OTP is delivered through SMTP/SES.
- [ ] Development OTP return is disabled.
- [ ] Default application secret is replaced.
- [ ] S3 public access is blocked.
- [ ] RDS is private and backed up.
- [ ] OpenAI and SMTP secrets are not in Git.
- [ ] Teaching and research outputs are reviewed.
- [ ] Audit events are visible.
- [ ] CloudWatch alerts are configured.
- [ ] Retention and deletion rules are approved.
