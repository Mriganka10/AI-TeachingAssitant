# Security, Privacy, and Academic Integrity

## Core Principle

Professor AI assists faculty; it does not replace academic judgement, peer review, assessment
governance, or responsibility for published and taught material.

## Sensitive Information

The platform may process:

- professor email identity
- unpublished research papers and drafts
- licensed books and case studies
- student-facing assessment material
- institutional notes and curriculum
- generated questions, answers, and rubrics
- research ideas and potential intellectual property

Treat all uploaded and generated material as confidential unless explicitly classified otherwise.

## Existing Controls

- Passwordless OTP authentication
- Hashed OTP and session values
- OTP expiry, single use, and attempt limit
- HTTP-only SameSite cookie
- Secure-cookie production option
- Tenant-scoped database queries
- Tenant-scoped object-storage paths
- S3 server-side encryption and optional KMS
- Upload extension and size validation
- SHA-256 file and artifact checksums
- Audit events for core user actions
- Production rejection of default application secret

## Required Production Controls

### Secrets

- Never commit `.env`, OpenAI keys, SMTP passwords, database credentials, or application secrets.
- Store secrets in AWS Secrets Manager, SSM Parameter Store, or protected EB properties.
- Use different values for `SECRET_KEY` and `OPENAI_API_KEY`.
- Rotate any key exposed in Git, screenshots, logs, or chat.

### Authentication

- Add OTP request rate limiting.
- Add account allow-listing or institutional-domain verification.
- Prefer Cognito or institutional SSO for broad deployment.
- Add session cleanup and explicit session/device management.
- Add CSRF protection to state-changing endpoints.

### Data Protection

- Use HTTPS everywhere.
- Block public S3 access.
- Encrypt S3 and RDS.
- Keep RDS private inside the VPC.
- Add malware scanning and MIME/content validation.
- Define retention and deletion policies.
- Avoid logging source text, model prompts, or generated content.

### Academic Integrity

- Label generated output as AI-assisted draft material.
- Require professor review before classroom or publication use.
- Verify citations, DOI, year, and author details.
- Do not generate undisclosed student submission content.
- Follow institutional policies for AI in assessment.
- Do not upload licensed material unless institutional use permits processing.

### Prompt and Source Safety

- Treat uploaded content as untrusted data, not system instructions.
- Keep system prompts separate from retrieved text.
- Do not let retrieved documents override application policy.
- Add source provenance to final output.
- Introduce citation verification before production research use.

## Incident Response

If a credential is exposed:

1. Revoke or rotate it immediately.
2. Remove it from the current working tree and Git history if committed.
3. Review provider usage and audit logs.
4. Replace affected deployment environment values.
5. document the incident and corrective action.

## Disclaimer

Generated content may contain errors or incomplete interpretations. Professors remain responsible
for teaching accuracy, assessment fairness, copyright compliance, research ethics, and publication
quality.
