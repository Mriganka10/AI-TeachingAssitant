import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.models import SourceDocument

CHUNK_CHARS = 4_000
CHUNK_OVERLAP = 500
MAX_CHUNKS_PER_DOCUMENT = 3


def retrieve_context(
    db: Session, *, tenant_id: str, query: str, collections: list[str], limit: int = 12
) -> list[dict]:
    """Retrieve focused, source-labelled chunks instead of whole-document prefixes."""
    statement = select(SourceDocument).where(
        SourceDocument.tenant_id == tenant_id,
        SourceDocument.status == "ready",
    )
    if collections:
        statement = statement.where(SourceDocument.collection.in_(collections))
    documents = list(db.scalars(statement.order_by(SourceDocument.created_at.desc()).limit(250)))

    candidates: list[tuple[SourceDocument, int, str]] = []
    for document in documents:
        for chunk_index, text in enumerate(_chunk_text(document.extracted_text)):
            candidates.append((document, chunk_index, text))
    if not candidates:
        return []

    scores = _semantic_scores(query, [item[2] for item in candidates])
    ranked = sorted(
        zip(candidates, scores, strict=True),
        key=lambda item: item[1],
        reverse=True,
    )
    selected: list[dict] = []
    per_document: Counter[str] = Counter()
    remaining = settings.max_context_chars
    for (document, chunk_index, text), score in ranked:
        if per_document[document.id] >= MAX_CHUNKS_PER_DOCUMENT:
            continue
        if selected and score <= 0:
            break
        excerpt = text[:remaining]
        if not excerpt:
            break
        selected.append(
            {
                "source_id": f"uploaded:{document.id}",
                "excerpt_id": f"uploaded:{document.id}:chunk:{chunk_index + 1}",
                "filename": document.filename,
                "collection": document.collection,
                "relevance_score": round(float(score), 4),
                "text": excerpt,
            }
        )
        per_document[document.id] += 1
        remaining -= len(excerpt)
        if len(selected) >= limit or remaining <= 0:
            break
    return selected


def _chunk_text(text: str) -> list[str]:
    cleaned = re.sub(r"\r\n?", "\n", text or "").strip()
    if not cleaned:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", cleaned) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > CHUNK_CHARS:
            if current:
                chunks.append(current)
                current = ""
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start : start + CHUNK_CHARS])
                start += CHUNK_CHARS - CHUNK_OVERLAP
            continue
        candidate = f"{current}\n\n{paragraph}".strip()
        if current and len(candidate) > CHUNK_CHARS:
            chunks.append(current)
            overlap = current[-CHUNK_OVERLAP:]
            current = f"{overlap}\n\n{paragraph}".strip()
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _semantic_scores(query: str, chunks: list[str]) -> list[float]:
    if not query.strip():
        return [1.0 / (index + 1) for index in range(len(chunks))]
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        matrix = TfidfVectorizer(
            lowercase=True,
            ngram_range=(1, 2),
            stop_words="english",
            sublinear_tf=True,
            max_features=40_000,
        ).fit_transform([query, *chunks])
        return cosine_similarity(matrix[0:1], matrix[1:]).ravel().tolist()
    except (ImportError, OSError, ValueError):
        terms = {term.lower() for term in re.findall(r"\w+", query) if len(term) > 2}
        return [
            float(sum(chunk.lower().count(term) for term in terms))
            for chunk in chunks
        ]
