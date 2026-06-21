from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.models import SourceDocument


def retrieve_context(
    db: Session, *, tenant_id: str, query: str, collections: list[str], limit: int = 12
) -> list[dict]:
    statement = select(SourceDocument).where(
        SourceDocument.tenant_id == tenant_id,
        SourceDocument.status == "ready",
    )
    if collections:
        statement = statement.where(SourceDocument.collection.in_(collections))
    documents = list(db.scalars(statement.order_by(SourceDocument.created_at.desc()).limit(250)))
    terms = {term.lower() for term in query.split() if len(term) > 3}
    ranked = sorted(
        documents,
        key=lambda doc: sum(doc.extracted_text.lower().count(term) for term in terms),
        reverse=True,
    )[:limit]
    remaining = settings.max_context_chars
    contexts = []
    for doc in ranked:
        text = doc.extracted_text[: min(20_000, remaining)]
        if not text:
            continue
        contexts.append(
            {"document_id": doc.id, "filename": doc.filename, "collection": doc.collection, "text": text}
        )
        remaining -= len(text)
        if remaining <= 0:
            break
    return contexts
