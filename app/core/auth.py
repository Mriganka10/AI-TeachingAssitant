import hashlib
import re
import secrets
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.models import AuthSession, EmailVerification, OTPChallenge, User

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


@dataclass(frozen=True)
class OTPRequestResult:
    email: str
    message: str
    status: str = "sent"
    delivery: str = "email"
    dev_otp: str | None = None


class AuthService:
    def request_otp(self, db: Session, email: str) -> OTPRequestResult:
        email = normalize_email(email)
        if self._ses_enabled and not settings.otp_dev_mode:
            verification = self.ensure_email_verified_or_start(db, email)
            if verification.status != "verified":
                return OTPRequestResult(
                    email=email,
                    message=(
                        "We sent an AWS SES verification link to this email. "
                        "Please click that link, then return here and request your OTP."
                    ),
                    status="verification_required",
                    delivery="verification-email",
                )
        code = f"{secrets.randbelow(1_000_000):06d}"
        db.add(
            OTPChallenge(
                email=email,
                otp_hash=secret_hash(code),
                expires_at=datetime.now(UTC) + timedelta(minutes=settings.otp_ttl_minutes),
            )
        )
        db.commit()
        sent = self._send_email(email, code)
        if not sent and settings.is_production and not settings.otp_dev_mode:
            raise HTTPException(status_code=503, detail="OTP email delivery failed.")
        return OTPRequestResult(
            email=email,
            message="OTP sent.",
            delivery="email" if sent else "server-log",
            dev_otp=code if settings.otp_dev_mode and not settings.is_production else None,
        )

    def register_email(self, db: Session, email: str) -> OTPRequestResult:
        email = normalize_email(email)
        verification = self.ensure_email_verified_or_start(db, email, force_start=True)
        if verification.status == "verified":
            return OTPRequestResult(
                email=email,
                message="Email is verified. You can request your OTP now.",
                status="verified",
                delivery="none",
            )
        return OTPRequestResult(
            email=email,
            message=(
                "AWS SES verification email requested. Open that email and click the "
                "verification link, then return here and request your OTP."
            ),
            status="verification_required",
            delivery="verification-email",
        )

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

    def ensure_email_verified_or_start(
        self, db: Session, email: str, *, force_start: bool = False
    ) -> OTPRequestResult:
        email = normalize_email(email)
        if not self._ses_enabled:
            self._save_email_verification(
                db,
                email,
                status="SUCCESS",
                provider=settings.email_provider,
                detail="Non-SES provider; verification bypassed.",
            )
            self._ensure_user(db, email)
            return OTPRequestResult(email=email, message="Email verified.", status="verified")

        status = self._ses_email_status(db, email)
        if status.upper() == "SUCCESS":
            self._ensure_user(db, email)
            return OTPRequestResult(email=email, message="Email verified.", status="verified")

        existing = db.scalar(select(EmailVerification).where(EmailVerification.email == email))
        if force_start or not existing or status.upper() in {"NOT_STARTED", "UNKNOWN", "FAILED"}:
            status = self._start_ses_email_verification(db, email)
        return OTPRequestResult(
            email=email,
            message="Email verification is pending.",
            status="pending",
            delivery=status,
        )

    def _send_email(self, email: str, code: str) -> bool:
        if self._ses_enabled:
            return self._send_email_ses(email, code)
        if not settings.smtp_host:
            if settings.is_production and not settings.otp_dev_mode:
                raise HTTPException(status_code=503, detail="OTP email service is not configured.")
            print(f"[professor-ai] OTP for {email}: {code}")
            return False
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
        return True

    def _send_email_ses(self, email: str, code: str) -> bool:
        from_email = settings.resolved_from_email
        if not from_email:
            print(f"[professor-ai] SES OTP delivery is not configured; OTP for {email}: {code}")
            return False
        subject = "Your Professor AI sign-in code"
        body = (
            f"Your Professor AI OTP is {code}.\n\n"
            f"It expires in {settings.otp_ttl_minutes} minutes. "
            "If you did not request this code, you can ignore this email."
        )
        try:
            self._ses_client().send_email(
                FromEmailAddress=from_email,
                Destination={"ToAddresses": [email]},
                Content={
                    "Simple": {
                        "Subject": {"Data": subject, "Charset": "UTF-8"},
                        "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
                    }
                },
            )
        except (BotoCoreError, ClientError) as exc:
            print(f"[professor-ai] SES OTP delivery failed for {email}: {exc}")
            return False
        return True

    def _start_ses_email_verification(self, db: Session, email: str) -> str:
        try:
            self._ses_client().create_email_identity(EmailIdentity=email)
            status = "PENDING"
            detail = "AWS SES verification email requested."
        except (BotoCoreError, ClientError) as exc:
            status = self._ses_email_status(db, email)
            detail = f"SES verification request did not complete: {exc}"
            if status.upper() not in {"SUCCESS", "PENDING", "TEMPORARY_FAILURE"}:
                self._save_email_verification(db, email, status=status, provider="ses", detail=detail)
                raise HTTPException(
                    status_code=503,
                    detail="Unable to start AWS SES email verification. Please try again later.",
                ) from exc
        self._save_email_verification(db, email, status=status, provider="ses", detail=detail)
        return status

    def _ses_email_status(self, db: Session, email: str) -> str:
        try:
            response = self._ses_client().get_email_identity(EmailIdentity=email)
            status = str(response.get("VerificationStatus") or "NOT_STARTED")
        except (BotoCoreError, ClientError) as exc:
            print(f"[professor-ai] SES verification status check failed for {email}: {exc}")
            status = "UNKNOWN"
        self._save_email_verification(
            db,
            email,
            status=status,
            provider="ses",
            detail="SES identity status checked.",
        )
        return status

    def _save_email_verification(
        self, db: Session, email: str, *, status: str, provider: str, detail: str = ""
    ) -> None:
        now = datetime.now(UTC)
        verified_at = now if status.upper() in {"SUCCESS", "VERIFIED"} else None
        row = db.scalar(select(EmailVerification).where(EmailVerification.email == email))
        if row:
            row.status = status
            row.provider = provider
            row.last_checked_at = now
            row.detail = detail
            if verified_at:
                row.verified_at = verified_at
        else:
            db.add(
                EmailVerification(
                    email=email,
                    status=status,
                    provider=provider,
                    verified_at=verified_at,
                    last_checked_at=now,
                    detail=detail,
                )
            )
        db.commit()

    def _ensure_user(self, db: Session, email: str) -> User:
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            user = User(email=email, tenant_id=tenant_for_email(email))
            db.add(user)
            db.commit()
        return user

    @property
    def _ses_enabled(self) -> bool:
        return settings.email_provider.strip().lower() == "ses"

    def _ses_client(self):
        import boto3

        return boto3.client("sesv2", region_name=settings.resolved_ses_region)

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
