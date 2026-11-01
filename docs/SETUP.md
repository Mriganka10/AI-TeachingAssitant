# Local Setup Guide

## Prerequisites

- Git
- Python 3.11, 3.12, or 3.13 recommended
- A valid OpenAI API key for live generation
- Optional, for video animations: Cairo and Pango development libraries
  (Debian/Ubuntu: `sudo apt install build-essential pkg-config libcairo2-dev libpango1.0-dev`;
  macOS: `brew install cairo pango pkg-config`)

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
VIDEO_GENERATION_ENABLED=true
VIDEO_MIN_MINUTES=4
VIDEO_MAX_MINUTES=15
VIDEO_SCRIPT_MODEL=
VIDEO_SCRIPT_MAX_OUTPUT_TOKENS=14000
VIDEO_TTS_PROVIDER=openai
VIDEO_TTS_MODEL=gpt-4o-mini-tts
VIDEO_TTS_VOICE=marin
VIDEO_TTS_SPEED=0.95
ELEVENLABS_API_KEY=
ELEVENLABS_MODEL_ID=eleven_multilingual_v2
ELEVENLABS_VOICE_ID=JBFqnCBsd6RMkjVDRZzb
ELEVENLABS_OUTPUT_FORMAT=mp3_44100_128
ELEVENLABS_STABILITY=0.55
ELEVENLABS_SIMILARITY_BOOST=0.78
VIDEO_SCRIPT_REVIEW_ENABLED=true
VIDEO_ILLUSTRATIONS_ENABLED=true
VIDEO_MAX_ILLUSTRATIONS=8
VIDEO_CAPTIONS_ENABLED=true
GEMINI_API_KEY=
GEMINI_IMAGE_MODEL=gemini-2.5-flash-image
GEMINI_IMAGE_TIMEOUT_SECONDS=90
VIDEO_ANIMATIONS_ENABLED=true
VIDEO_MAX_ANIMATIONS=4
VIDEO_ANIMATION_RENDER_TIMEOUT_SECONDS=180
VIDEO_ANIMATION_REPAIR_ATTEMPTS=1
VIDEO_CINEMATIC_CLIPS=false
VIDEO_MAX_CINEMATIC_CLIPS=2
VIDEO_CINEMATIC_SECONDS=8
VEO_MODEL=veo-3.1-lite-generate-preview
VEO_RESOLUTION=720p
VEO_TIMEOUT_SECONDS=420
VEO_POLL_INTERVAL_SECONDS=10
RESEARCH_MAX_MANUSCRIPT_CHARS=220000
RESEARCH_RELATED_CONTEXT_CHARS=40000
RESEARCH_REVISION_WORKERS=4
RESEARCH_REVISION_MAX_WORDS=1600
RESEARCH_REASONING_EFFORT=medium
RESEARCH_MAX_NEW_SOURCES=12
RESEARCH_MAX_NEW_SECTIONS=4
RESEARCH_SOURCE_TIMEOUT_SECONDS=15
RESEARCH_CONTACT_EMAIL=
```

Research assistant settings (all optional):

- `RESEARCH_MAX_MANUSCRIPT_CHARS` limits how much of a manuscript is reviewed (about 35,000 words
  by default). Longer papers are reviewed up to the limit and the report says so.
- `RESEARCH_RELATED_CONTEXT_CHARS` limits how much of the research-paper library is sent with the
  manuscript for literature positioning.
- `RESEARCH_REVISION_WORKERS` sets how many sections are revised at the same time, and
  `RESEARCH_REVISION_MAX_WORDS` the largest block revised in one call. Lower the workers if you hit
  OpenAI rate limits.
- `RESEARCH_REASONING_EFFORT` sets how deeply the review and revision calls reason (`low`,
  `medium` or `high`; higher is slower and costs more). `RESEARCH_MAX_NEW_SOURCES` and
  `RESEARCH_MAX_NEW_SECTIONS` cap added references and sections. New references are confirmed
  against `api.crossref.org` and `export.arxiv.org`, so the server must be able to reach them;
  set `RESEARCH_CONTACT_EMAIL` to use Crossref's faster "polite" pool.
- Exported review and manuscript PDFs use Carlito, Caladea and Liberation fonts when installed
  (metric-compatible with Calibri, Cambria and Times New Roman). The Docker image installs them;
  locally, install `fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation` or the
  PDFs fall back to DejaVu.

Video quality settings (all optional):

- `VIDEO_SCRIPT_REVIEW_ENABLED` adds a second "senior teacher" pass over the video script. The
  reviewed script is used only if it still passes every check, so it can't make a video worse.
- `GEMINI_API_KEY` turns on illustrated scenes (Google AI Studio key). Without it, videos use the
  drawn diagrams exactly as before. `VIDEO_MAX_ILLUSTRATIONS` caps image calls per video (about
  $0.04–0.07 per image at current list prices). `GEMINI_IMAGE_MODEL` can be switched to a newer
  image model, such as `gemini-3.1-flash-image`, without code changes.
- `VIDEO_CAPTIONS_ENABLED` draws captions below the lesson card and adds a switchable subtitle
  track to the MP4.
- `VIDEO_ANIMATIONS_ENABLED` adds Manim animations to maths, graph, physics and algorithm
  scenes. It works only when Manim is installed: `pip install -e ".[animations]"` after the Cairo
  and Pango libraries above. Without Manim, these scenes use drawn diagrams and nothing else
  changes. `VIDEO_MAX_ANIMATIONS` caps animated scenes per video (each costs one extra model call,
  two if it needs a repair, and about 10 to 60 seconds of rendering).
  `VIDEO_ANIMATION_RENDER_TIMEOUT_SECONDS` stops a slow render, and
  `VIDEO_ANIMATION_REPAIR_ATTEMPTS` sets how many times a failed render is sent back for a fix.
  Installing LaTeX (`texlive-latex-base texlive-latex-extra texlive-fonts-recommended dvisvgm`)
  lets animations typeset formulas; without it they write formulas as plain text.

- `VIDEO_CINEMATIC_CLIPS=true` brings a few picture scenes to life with short Veo clips (the
  opening scene first): the scene's illustration is the first frame and gently moves while the
  narrator speaks. It uses the same `GEMINI_API_KEY` and needs illustrations to be on. It is off
  by default because it is billed per second of video: with the default Lite model at 720p, an
  8-second clip is about $0.40, so the default cap of 2 clips adds about $0.80 per video. Check
  current Gemini API prices before changing `VEO_MODEL` (Fast and standard Veo cost more). Clips
  generate while narration is recorded; any clip not ready within `VEO_TIMEOUT_SECONDS`, or that
  fails, leaves the still picture. `VIDEO_CINEMATIC_SECONDS` can be 4, 6 or 8.

Docker images include animations by default. Build with `--build-arg WITH_ANIMATIONS=false` for
a smaller image without them, or `--build-arg WITH_LATEX=true` to add LaTeX.

`imageio-ffmpeg` installs the video encoder with the Python dependencies. Multimedia video is
opt-in in the Teaching Assistant form and is not created for document-only requests. OpenAI builds
the grounded storyboard. Set `VIDEO_TTS_PROVIDER=elevenlabs` and provide `ELEVENLABS_API_KEY` plus
a voice ID for ElevenLabs long-form narration; otherwise OpenAI TTS is used. Mock mode does not
create a silent placeholder because that could be mistaken for a finished explanatory video. Leave
`VIDEO_SCRIPT_MODEL` blank to use `OPENAI_MODEL` for the structured storyboard.

The default ElevenLabs format is `mp3_44100_128`, which the application converts to WAV locally
before video assembly. Do not set `pcm_44100` unless the ElevenLabs account is on Pro or above.
Keep `VIDEO_TTS_SPEED` between `0.75` and `1.20`; the default `0.95` is recommended for clear
academic narration. The renderer rejects unexpectedly short narration instead of returning a
one-minute or mostly silent lesson. It rebuilds the final audio/video timeline, verifies that the
MP4 is at least `VIDEO_MIN_MINUTES`, builds each diagram up step by step, and uses animated
transitions between the storyboard's distinct scenes.
Speech services may return a WAV with a long silent tail. The application now measures audible
samples, trims that tail, allocates time to every planned scene, and checks the completed MP4 for
visual progression. If a provider returns too little usable speech, the video reports a warning
while JSON, DOCX, PDF, and PPTX remain available. Existing MP4 downloads are not changed; generate
a new teaching job to test the updated renderer.

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
