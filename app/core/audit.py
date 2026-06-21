from fastapi import Request
from sqlalchemy.orm import Session

from app.core.models import AuditEvent


def audit(
    db: Session,
    *,
    tenant_id: str,
    actor_email: str,
    event_type: str,
    status: str,
    request: Request | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict | None = None,
) -> None:
    db.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor_email=actor_email,
            event_type=event_type,
            status=status,
            entity_type=entity_type,
            entity_id=entity_id,
            request_id=request.headers.get("x-request-id") if request else None,
            ip_address=request.client.host if request and request.client else None,
            details=details or {},
        )
    )
    db.commit()
