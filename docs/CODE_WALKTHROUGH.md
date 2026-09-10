# Code Walkthrough

Last updated: 10 September 2026. This walkthrough describes the code currently deployed at `https://professoraihub.com`.

## Runtime entry points

- `app/main.py` creates the FastAPI web application, initializes persistence, mounts the UI, and exposes health and API routes.
- `app/api/routes.py` owns authentication, uploads, agent requests, job status, and artifact downloads.
- `app/worker.py` is the production SQS consumer. The same container image starts it when `SERVICE_MODE=worker`.
- `Dockerfile` selects web or worker mode. Web starts migrations and Uvicorn; worker starts `python -m app.worker`.

## Teaching and research request flow

1. The browser authenticates through the OTP/session routes in `app/api/routes.py`.
2. Uploads are validated and stored through `app/core/documents.py` and `app/core/storage.py`. Production objects use tenant-prefixed private S3 keys.
3. An agent endpoint writes an `AgentJob` row and its request payload before returning the job identifier.
4. With `AGENT_EXECUTION_BACKEND=sqs`, `app/core/job_queue.py` sends only `{"job_id": "..."}` to the configured queue. Local development can retain `background` execution.
5. `app/worker.py` long-polls SQS, calls `run_agent_job(job_id)`, and deletes the message only after successful processing. Failed messages remain available for retry and ultimately reach the DLQ.
6. `run_agent_job` reloads tenant and payload state from PostgreSQL, ignores already-completed jobs, runs retrieval and the selected agent, generates artifacts, and records completion or failure plus audit data.
7. The UI polls the job endpoint and receives private artifact download links after completion.

## Important modules

- `app/agents/retrieval.py`: tenant-scoped context selection.
- `app/agents/prompts.py` and `app/agents/llm.py`: prompt construction and model invocation.
- `app/artifacts/generator.py`: JSON, DOCX, PDF, and PPTX output.
- `app/core/models.py` and `app/core/database.py`: relational state and sessions.
- `app/core/auth.py`: OTP and signed-session security.
- `app/core/audit.py`: security and execution audit events.
- `app/core/config.py`: environment-backed runtime configuration.

## Recent functional and reliability changes

- Long-running agent work moved out of the web process in production to a durable SQS worker.
- Queue publication failure is visible to the caller as HTTP 503 and the job is marked failed rather than remaining indefinitely queued.
- Job execution is idempotent at the job boundary, making message redelivery safe.
- The database migration path now tolerates both a clean database and an existing schema baseline.
- User-facing routes, payloads, polling behavior, artifacts, and the public domain did not change.

## Configuration affecting the flow

`AGENT_EXECUTION_BACKEND`, `AGENT_QUEUE_URL`, `AGENT_QUEUE_WAIT_SECONDS`, `AGENT_QUEUE_VISIBILITY_SECONDS`, `SERVICE_MODE`, `DATABASE_URL`, `STORAGE_PROVIDER`, `S3_BUCKET`, and the existing authentication/model variables control deployment behavior. Secrets must come from AWS secret/configuration stores and must not be committed.

## Verification

Run `pytest` and `ruff check .`. On 10 September 2026, all 15 tests passed. The repository-wide
Ruff audit still reports 28 pre-existing findings (primarily FastAPI dependency defaults, broad
exception handling, and migration import/style rules); do not describe lint as clean until those
are resolved in a dedicated code-quality change.
