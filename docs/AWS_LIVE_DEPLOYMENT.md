# AWS Live Deployment

Deployment date: 22 June 2026  
Region: `ap-south-1`  
Source branch: `release_branch`  
Deployed release: `b0cfaa0`

## Public Endpoint

`http://ai-teaching-assistant-prod.ap-south-1.elasticbeanstalk.com`

Health endpoint:

`http://ai-teaching-assistant-prod.ap-south-1.elasticbeanstalk.com/health`

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
- Artifact download through the authenticated endpoint: verified

## Current Authentication Mode

The environment currently uses staging/development OTP behavior because SMTP/SES credentials have
not been supplied. Before opening the service to real faculty users:

1. configure Amazon SES or another SMTP provider;
2. set `ENVIRONMENT=production` and `OTP_DEV_MODE=false`;
3. attach an ACM certificate through a load-balanced Beanstalk environment or a custom HTTPS
   endpoint;
4. set `COOKIE_SECURE=true`.

No credentials or API keys are stored in this document or committed to the repository.
