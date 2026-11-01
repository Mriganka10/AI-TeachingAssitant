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
- research endpoint requires an uploaded manuscript; unknown agent types fail cleanly
- research review end to end in mock mode: upload, review, revision, six artifacts, job list
- manuscript parsing from PDF and DOCX (title, sections, references, running headers removed)
- review contract checks (unknown locations, inconsistent decisions)
- revision integrity checks (lost or invented citations and numbers, author placeholders)
- failed revisions keep the original text; padded or unsupported added sections are dropped
- new references: Crossref and arXiv parsing, invented or mismatched DOIs rejected, registry
  metadata used, duplicates and already-cited works skipped, IEEE and APA formatting
- revision plan normalisation and heading renumbering; citations in numeric and author-year
  styles; numbered reference lists never reordered
- invented numbers caught and repaired; tables and keywords in clean and tracked exports
- tracked-changes DOCX contains real Word revisions with unique IDs
- research exports carry no tool names or AI labels in text or metadata
- expected artifact formats
- async job polling endpoint
- failed async job behavior
- job title metadata

## Manual Faculty UI Test

1. Open the login page.
2. Enter an institutional email.
3. Request and verify local OTP.
4. Confirm the home page greeting, lesson counts and setup checklist.
5. Upload one file into each collection under Course materials (try drag and drop too).
6. Open New lesson; check the length quick-picks, level selector and video toggle.
7. Create a mock or live lesson.
8. Confirm the progress tracker moves through writing, building files (and video) to ready.
9. Download the teacher's guide (DOCX, PDF) and student slides (PPTX).
10. Refresh the lesson page and confirm it reloads the same lesson.
11. Open My lessons and reopen the lesson.
12. Confirm the home counts and recent lessons update.
13. Check the layout at phone width.
14. Sign out and verify protected endpoints reject the old session.

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
- [ ] PDF and PPTX text stays on the requested topic; video scene titles,
      visuals, and narration follow the validated teaching package.
- [ ] Documentation commands match the repository.
- [ ] No secrets are staged.
- [ ] Deployment variables are documented.
- [ ] Database backup exists.
- [ ] Rollback version is identified.
