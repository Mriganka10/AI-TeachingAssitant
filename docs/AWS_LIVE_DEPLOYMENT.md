# AWS Live Deployment

Initial deployment date: 22 June 2026  
Latest production update: 17 August 2026  
Region: `ap-south-1`  
Source branch: `release_branch`  
Deployed release: `47e8810` — async agent generation and job polling

## Public Endpoint

Primary domain:

`https://professoraihub.com`

Elastic Beanstalk origin:

`http://ai-teaching-assistant-prod.ap-south-1.elasticbeanstalk.com`

Health endpoint:

`https://professoraihub.com/health`

## Resource Inventory

| Component | Resource |
| --- | --- |
| Elastic Beanstalk application | `ai-teaching-assistant-prod-app` |
| Elastic Beanstalk environment | `ai-teaching-assistant-prod` |
| EC2 instance type | `t3.small` |
| VPC | `vpc-03ebc834e9332dbb9` |
| Public subnets | `subnet-01d38645d64592342`, `subnet-0af3fa6252b0a6a53` |
| Private database subnets | `subnet-0e09e1af80cb62285`, `subnet-0679be0175cb8f7d2` |
| Application security group | `sg-0aad3fa647d4d0799` |
| Database security group | `sg-0080bf7323c1eb241` |
| PostgreSQL instance | `ai-teaching-assistant-prod-db` |
| PostgreSQL engine | PostgreSQL 18.3, encrypted, private, deletion-protected |
| S3 bucket | `ai-teaching-assistant-prod-453732174568-ap-south-1` |
| CloudFront distribution | `E2BBR56BCXF4NL` |
| CloudFront domain | `d2r0ksdo6bzlza.cloudfront.net` |
| Route 53 hosted zone | `Z03261912TTDFCC1BX1H6` |
| Custom domain | `professoraihub.com` |
| Database credentials | AWS Secrets Manager secret `ai-teaching-assistant-prod/database` |
| EB service role | `ai-teaching-assistant-prod-eb-service-role` |
| EC2 role | `ai-teaching-assistant-prod-ec2-role` |

All resources are dedicated to this application and tagged with
`Project=AI-Teaching-Assistant`.

## Verification

- Elastic Beanstalk status: `Ready`
- Enhanced health: `Green / Ok`
- Public health request: HTTP 200
- OTP login and professor user creation: verified
- PostgreSQL Alembic revision: `20260622_0001`
- Audit, user, source-document, job, and artifact tables: verified
- S3 RAG upload: verified
- Teaching Assistant OpenAI run: verified with JSON, DOCX, PDF, and PPTX artifacts
- Research Assistant OpenAI run: verified with JSON, DOCX, PDF, and PPTX artifacts
- Agent generation flow: verified as queued job plus `GET /api/jobs/{job_id}` polling
- Artifact download through the authenticated endpoint: verified

## Current Authentication Mode

The environment supports first-time professor email verification and OTP delivery through the
configured production email provider. For Amazon SES, the sender/domain identities must remain
verified and the environment must keep production-safe values:

1. `ENVIRONMENT=production`;
2. `OTP_DEV_MODE=false`;
3. `COOKIE_SECURE=true`;
4. `EMAIL_PROVIDER=ses` or configured SMTP settings;
5. verified sender such as `no-reply@professoraihub.com`.

## Current Agent Execution Mode

The deployed release uses in-process FastAPI background tasks for agent generation. The browser
gets a `job_id` immediately and polls job status until JSON, DOCX, PDF, and PPTX artifacts are
available. This avoids CloudFront/Nginx timeout HTML pages for normal long-running agent work.

For higher concurrency, migrate this same job contract to SQS plus worker instances without
changing the professor-facing API shape.

No credentials or API keys are stored in this document or committed to the repository.
