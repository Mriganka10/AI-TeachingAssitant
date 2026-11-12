FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Video animations (Manim) are on by default; build with --build-arg WITH_ANIMATIONS=false to
# leave them out. WITH_LATEX=true adds LaTeX so animations can typeset formulas (larger image).
ARG WITH_ANIMATIONS=true
ARG WITH_LATEX=false

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr fonts-dejavu-core \
       fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea \
    && if [ "$WITH_ANIMATIONS" = "true" ]; then \
         apt-get install -y --no-install-recommends build-essential pkg-config \
           libcairo2-dev libpango1.0-dev; \
       fi \
    && if [ "$WITH_ANIMATIONS" = "true" ] && [ "$WITH_LATEX" = "true" ]; then \
         apt-get install -y --no-install-recommends texlive-latex-base texlive-latex-extra \
           texlive-fonts-recommended dvisvgm; \
       fi \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
RUN if [ "$WITH_ANIMATIONS" = "true" ]; then pip install --no-cache-dir ".[animations]"; \
    else pip install --no-cache-dir .; fi
RUN mkdir -p /app/data

EXPOSE 8000
CMD ["sh", "-c", "if [ \"${SERVICE_MODE:-web}\" = \"worker\" ]; then exec python -m app.worker; else alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers; fi"]
