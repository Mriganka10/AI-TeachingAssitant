# Testing and Quality Assurance

## Automated Checks

```bash
python -m ruff check .
python -m pytest -q
```

Current integration tests cover:

- health endpoint
- OTP request and verification
- authenticated user endpoint
- source upload
- teaching agent in mock mode
- research agent in mock mode
- expected artifact formats
- async job polling endpoint
- failed async job behavior
- job title metadata

## Manual Faculty UI Test

1. Open the login page.
2. Enter an institutional email.
3. Request and verify local OTP.
4. Confirm professor-specific dashboard text and metrics.
5. Upload one source into each relevant collection.
6. Open Teaching Assistant.
7. Generate a mock or live lecture package.
8. Confirm the UI shows queued/running status rather than blocking indefinitely.
9. Download JSON, DOCX, PDF, and PPTX.
10. Open Research Paper Assistant.
11. Generate a research synthesis.
12. Confirm the UI polls until the job is completed.
13. Download JSON, DOCX, PDF, and PPTX.
14. Confirm recent activity and readiness metrics update.
15. Sign out and verify protected endpoints reject the old session.

## Live OpenAI Acceptance Test

Use a low-risk topic and verify:

- response is valid JSON
- all required output keys exist
- sources are not fabricated
- web search can be disabled and enabled
- latency is acceptable
- failed generation produces a failed job with a bounded error message
- the browser never displays raw `Unexpected token '<'` JSON parsing errors

Never put the API key in test fixtures or screenshots.

## Artifact QA

### Teaching

- PPTX opens and slide titles are readable.
- lecture-section slides contain expected bullet points.
- DOCX and PDF include all major sections.
- filenames are safe and bounded.

### Research

- themes and methodology comparison are readable.
- references are clearly separated.
- research gaps are framed as evidence-based claims.
- all cited references are manually verified.

## Security QA

- Cross-tenant artifact ID returns 404.
- Session cookie is HTTP-only.
- OTP expires and cannot be reused.
- Sixth incorrect OTP attempt does not verify.
- Oversized file returns 413.
- Unsupported extension returns 415.
- Production cannot start with default `SECRET_KEY`.
- S3 objects are not public.

## Pre-Release Checklist

- [ ] Ruff passes.
- [ ] Tests pass.
- [ ] Local UI flow passes.
- [ ] Live OpenAI smoke test passes.
- [ ] Documentation commands match the repository.
- [ ] No secrets are staged.
- [ ] Deployment variables are documented.
- [ ] Database backup exists.
- [ ] Rollback version is identified.
