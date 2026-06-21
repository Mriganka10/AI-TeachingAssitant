import hashlib
import re
import secrets
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.models import AuthSession, OTPChallenge, User

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def secret_hash(value: str) -> str:
    return hashlib.sha256(f"{settings.secret_key}:{value}".encode()).hexdigest()


def normalize_email(email: str) -> str:
    value = email.strip().lower()
    if not EMAIL_RE.match(value):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    return value


def tenant_for_email(email: str) -> str:
    domain = email.split("@", 1)[-1]
    return re.sub(r"[^a-z0-9-]", "-", domain)[:100]


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    tenant_id: str
    role: str


class AuthService:
    def request_otp(self, db: Session, email: str) -> tuple[str, str | None]:
        email = normalize_email(email)
        code = f"{secrets.randbelow(1_000_000):06d}"
        db.add(
            OTPChallenge(
                email=email,
                otp_hash=secret_hash(code),
                expires_at=datetime.now(UTC) + timedelta(minutes=settings.otp_ttl_minutes),
            )
        )
        db.commit()
        self._send_email(email, code)
        return email, code if settings.otp_dev_mode and not settings.is_production else None

    def verify_otp(self, db: Session, email: str, code: str) -> tuple[CurrentUser, str]:
        email = normalize_email(email)
        challenge = db.scalar(
            select(OTPChallenge)
            .where(OTPChallenge.email == email, OTPChallenge.consumed_at.is_(None))
            .order_by(OTPChallenge.created_at.desc())
        )
        now = datetime.now(UTC)
        if not challenge or self._aware(challenge.expires_at) <= now or challenge.attempts >= 5:
            raise HTTPException(status_code=401, detail="Invalid or expired OTP.")
        challenge.attempts += 1
        if not secrets.compare_digest(challenge.otp_hash, secret_hash(code.strip())):
            db.commit()
            raise HTTPException(status_code=401, detail="Invalid or expired OTP.")
        challenge.consumed_at = now
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            user = User(email=email, tenant_id=tenant_for_email(email))
            db.add(user)
            db.flush()
        user.last_login_at = now
        token = secrets.token_urlsafe(48)
        db.add(
            AuthSession(
                session_hash=secret_hash(token),
                user_id=user.id,
                expires_at=now + timedelta(minutes=settings.session_ttl_minutes),
            )
        )
        db.commit()
        return CurrentUser(user.id, user.email, user.tenant_id, user.role), token

    def logout(self, db: Session, token: str | None) -> None:
        if not token:
            return
        session = db.scalar(select(AuthSession).where(AuthSession.session_hash == secret_hash(token)))
        if session:
            session.revoked_at = datetime.now(UTC)
            db.commit()

    def _send_email(self, email: str, code: str) -> None:
        if not settings.smtp_host:
            if settings.is_production and not settings.otp_dev_mode:
                raise HTTPException(status_code=503, detail="OTP email service is not configured.")
            return
        message = EmailMessage()
        message["Subject"] = "Your Professor AI sign-in code"
        message["From"] = settings.smtp_from
        message["To"] = email
        message.set_content(f"Your one-time sign-in code is {code}. It expires shortly.")
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)


auth_service = AuthService()


def current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    if not settings.auth_enabled:
        return CurrentUser("local", "professor@local.dev", "local-dev", "professor")
    token = request.cookies.get(settings.cookie_name)
    if not token:
        raise HTTPException(status_code=401, detail="Sign in required.")
    row = db.execute(
        select(AuthSession, User)
        .join(User, AuthSession.user_id == User.id)
        .where(
            AuthSession.session_hash == secret_hash(token),
            AuthSession.revoked_at.is_(None),
        )
    ).first()
    if not row or AuthService._aware(row.AuthSession.expires_at) <= datetime.now(UTC):
        raise HTTPException(status_code=401, detail="Session expired.")
    user = row.User
    return CurrentUser(user.id, user.email, user.tenant_id, user.role)
