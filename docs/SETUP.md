# Local Setup Guide

## Prerequisites

- Git
- Python 3.11, 3.12, or 3.13 recommended
- A valid OpenAI API key for live generation

Python 3.12 matches the Docker image and is the preferred local version.

## Clone and Select Branch

```bash
git clone https://github.com/Mriganka10/AI-TeachingAssitant.git
cd AI-TeachingAssitant
git checkout release_branch
```

## Create Environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The repository defines `dev`; it does not define a `docs` optional dependency group.

## Configure

```bash
cp .env.example .env
```

Set at minimum:

```text
SECRET_KEY=<long-random-application-secret>
OPENAI_API_KEY=<valid-openai-api-key>
LLM_SERVICE_MODE=openai
```

Generate an application secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`SECRET_KEY` is an application hashing secret. It must not be an OpenAI key.

For local testing without OpenAI:

```text
LLM_SERVICE_MODE=mock
```

## Run

```bash
python -m uvicorn app.main:app --reload
```

Do not use `job_hunting_agent.web:app`; that module belongs to a different repository.

Open:

- Faculty UI: `http://127.0.0.1:8000`
- API docs: `http://127.0.0.1:8000/docs`
- Health: `http://127.0.0.1:8000/health`

## Local OTP

With:

```text
ENVIRONMENT=local
OTP_DEV_MODE=true
```

the OTP is returned to the browser for development convenience.

Production must use:

```text
ENVIRONMENT=production
OTP_DEV_MODE=false
COOKIE_SECURE=true
EMAIL_PROVIDER=ses
SES_FROM=<verified-sender>
```

SMTP is also supported by configuring `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and
`SMTP_FROM`.

## Tests and Lint

```bash
python -m pytest -q
python -m ruff check .
```

## Reset Local Data

Stop the server first. To start with a fresh local database and generated files:

```bash
rm -rf data/professor_ai.db data/professor-ai data/generated data/downloads
```

Do not run this against production storage.

## Common Issues

### `No module named job_hunting_agent`

Use:

```bash
python -m uvicorn app.main:app --reload
```

### `OPENAI_API_KEY is required`

Set the key in `.env`, or use `LLM_SERVICE_MODE=mock`.

### `Incorrect API key`

Create or rotate a key in the OpenAI platform and replace the local environment value. Never
commit the key.

### PDF contains no text

The PDF may be scanned. Text PDFs are extracted natively. For scanned PDFs, enable OCR and install
the local OCR dependencies for development, or configure Amazon Textract in AWS:

```text
OCR_ENABLED=true
OCR_PROVIDER=local      # local development
OCR_PROVIDER=textract   # AWS production
```

### OTP is not delivered

For local testing, enable `OTP_DEV_MODE=true`. For production, configure SMTP or Amazon SES SMTP.

### Cookie works locally but not in production

Use HTTPS and `COOKIE_SECURE=true`. A secure cookie will not be stored over plain HTTP.
