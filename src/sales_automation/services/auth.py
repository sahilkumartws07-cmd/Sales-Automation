from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
import hashlib
import hmac
import json
import re
import secrets
import smtplib
from typing import Any

from sqlalchemy.orm import Session

from sales_automation.config import Settings, get_settings
from sales_automation.models import EmailVerificationOTP, PasswordResetOTP, RefreshToken, User

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_HASH_ITERATIONS = 260_000
TOKEN_VERSION = "v1"


class AuthServiceError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class EmailConfigurationError(AuthServiceError):
    pass


def normalize_email(email: str) -> str:
    parsed = parseaddr(email.strip())[1].lower()
    if not parsed or not EMAIL_RE.match(parsed):
        raise AuthServiceError("Enter a valid email address.")
    return parsed


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_HASH_ITERATIONS
    )
    return "$".join(
        [
            "pbkdf2_sha256",
            str(PASSWORD_HASH_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = base64.b64decode(salt_raw.encode("ascii"))
        expected = base64.b64decode(digest_raw.encode("ascii"))
    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


class AuthService:
    def __init__(self, db: Session, *, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()

    def register_user(self, *, full_name: str, email: str, password: str) -> User:
        normalized_email = normalize_email(email)
        user = self._user_by_email(normalized_email)
        if user and user.is_active:
            raise AuthServiceError("An active account already exists for this email.", status_code=409)

        now = datetime.now(UTC)
        if user is None:
            user = User(
                full_name=full_name.strip(),
                email=normalized_email,
                password_hash=hash_password(password),
                is_active=False,
            )
            self.db.add(user)
            self.db.flush()
        else:
            user.full_name = full_name.strip()
            user.password_hash = hash_password(password)
            user.is_active = False
            user.verified_at = None
            self._consume_open_otps(user.id, now=now)

        otp = self._create_otp(user.id, now=now)
        self.db.flush()
        self._send_otp_email(to_email=user.email, full_name=user.full_name, otp=otp)
        return user

    def verify_otp(self, *, email: str, otp: str) -> User:
        normalized_email = normalize_email(email)
        user = self._user_by_email(normalized_email)
        if user is None:
            raise AuthServiceError("Invalid email or OTP.", status_code=400)
        if user.is_active:
            raise AuthServiceError("Account is already verified.", status_code=409)

        now = datetime.now(UTC)
        otp_record = (
            self.db.query(EmailVerificationOTP)
            .filter(EmailVerificationOTP.user_id == user.id)
            .filter(EmailVerificationOTP.consumed_at.is_(None))
            .order_by(EmailVerificationOTP.created_at.desc())
            .first()
        )
        if otp_record is None:
            raise AuthServiceError("No active OTP found. Please register again.", status_code=400)
        if otp_record.expires_at <= now:
            otp_record.consumed_at = now
            raise AuthServiceError("OTP has expired. Please register again.", status_code=400)
        if otp_record.attempts >= self.settings.otp_max_attempts:
            otp_record.consumed_at = now
            raise AuthServiceError("Too many OTP attempts. Please register again.", status_code=429)

        otp_record.attempts += 1
        if not self._verify_otp_value(otp, otp_record.otp_hash):
            raise AuthServiceError("Invalid email or OTP.", status_code=400)

        otp_record.consumed_at = now
        user.is_active = True
        user.verified_at = now
        return user

    def login(self, *, email: str, password: str) -> tuple[User, str, str]:
        normalized_email = normalize_email(email)
        user = self._user_by_email(normalized_email)
        if user is None or not verify_password(password, user.password_hash):
            raise AuthServiceError("Invalid email or password.", status_code=401)
        if not user.is_active:
            raise AuthServiceError("Please verify your email before logging in.", status_code=403)

        now = datetime.now(UTC)
        user.last_login_at = now
        token = self._create_access_token(
            {"sub": str(user.id), "email": user.email},
            expires_at=now + timedelta(minutes=self.settings.auth_token_expiry_minutes),
        )
        refresh_token = self._create_refresh_token(user.id, now=now)
        return user, token, refresh_token

    def refresh_access_token(self, *, refresh_token: str) -> tuple[User, str, str]:
        token_hash = self._hash_refresh_token(refresh_token)
        now = datetime.now(UTC)
        stored_token = (
            self.db.query(RefreshToken)
            .filter(RefreshToken.token_hash == token_hash)
            .first()
        )
        if stored_token is None:
            raise AuthServiceError("Invalid refresh token.", status_code=401)
        if stored_token.revoked_at is not None:
            raise AuthServiceError("Refresh token has been revoked.", status_code=401)
        if stored_token.expires_at <= now:
            stored_token.revoked_at = now
            raise AuthServiceError("Refresh token has expired.", status_code=401)

        user = stored_token.user
        if user is None or not user.is_active:
            stored_token.revoked_at = now
            raise AuthServiceError("User account is not active.", status_code=403)

        access_token = self._create_access_token(
            {"sub": str(user.id), "email": user.email},
            expires_at=now + timedelta(minutes=self.settings.auth_token_expiry_minutes),
        )
        new_refresh_token = self._create_refresh_token(user.id, now=now)
        stored_token.revoked_at = now
        stored_token.replaced_by_hash = self._hash_refresh_token(new_refresh_token)
        return user, access_token, new_refresh_token

    def authenticate_access_token(self, token: str) -> User:
        payload = self._decode_access_token(token)
        user_id = payload.get("sub")
        if not user_id:
            raise AuthServiceError("Invalid access token.", status_code=401)

        try:
            user = self.db.get(User, int(user_id))
        except (TypeError, ValueError):
            raise AuthServiceError("Invalid access token.", status_code=401) from None
        if user is None or not user.is_active:
            raise AuthServiceError("User account is not active.", status_code=403)
        return user

    def request_password_reset(self, *, email: str) -> str:
        normalized_email = normalize_email(email)
        user = self._user_by_email(normalized_email)
        if user is None or not user.is_active:
            raise AuthServiceError("No verified account found for this email.", status_code=404)

        now = datetime.now(UTC)
        self._consume_open_password_reset_otps(user.id, now=now)
        otp = self._create_password_reset_otp(user.id, now=now)
        self.db.flush()
        self._send_otp_email(
            to_email=user.email,
            full_name=user.full_name,
            otp=otp,
            subject="Reset your Sales Automation password",
            intro="Use this OTP to reset your password.",
        )
        return user.email

    def reset_password(
        self,
        *,
        email: str,
        otp: str,
        new_password: str,
    ) -> User:
        normalized_email = normalize_email(email)
        user = self._user_by_email(normalized_email)
        if user is None or not user.is_active:
            raise AuthServiceError("Invalid email or OTP.", status_code=400)

        now = datetime.now(UTC)
        otp_record = (
            self.db.query(PasswordResetOTP)
            .filter(PasswordResetOTP.user_id == user.id)
            .filter(PasswordResetOTP.consumed_at.is_(None))
            .order_by(PasswordResetOTP.created_at.desc())
            .first()
        )
        if otp_record is None:
            raise AuthServiceError("No active reset OTP found. Please request a new OTP.", status_code=400)
        if otp_record.expires_at <= now:
            otp_record.consumed_at = now
            raise AuthServiceError("OTP has expired. Please request a new OTP.", status_code=400)
        if otp_record.attempts >= self.settings.otp_max_attempts:
            otp_record.consumed_at = now
            raise AuthServiceError("Too many OTP attempts. Please request a new OTP.", status_code=429)

        otp_record.attempts += 1
        if not self._verify_otp_value(otp, otp_record.otp_hash):
            raise AuthServiceError("Invalid email or OTP.", status_code=400)

        otp_record.consumed_at = now
        user.password_hash = hash_password(new_password)
        return user

    def _user_by_email(self, email: str) -> User | None:
        return self.db.query(User).filter(User.email == email).first()

    def _create_otp(self, user_id: int, *, now: datetime) -> str:
        otp = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = now + timedelta(minutes=self.settings.otp_expiry_minutes)
        self.db.add(
            EmailVerificationOTP(
                user_id=user_id,
                otp_hash=self._hash_otp_value(otp),
                expires_at=expires_at,
            )
        )
        return otp

    def _create_password_reset_otp(self, user_id: int, *, now: datetime) -> str:
        otp = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = now + timedelta(minutes=self.settings.otp_expiry_minutes)
        self.db.add(
            PasswordResetOTP(
                user_id=user_id,
                otp_hash=self._hash_otp_value(otp),
                expires_at=expires_at,
            )
        )
        return otp

    def _create_refresh_token(self, user_id: int, *, now: datetime) -> str:
        token = secrets.token_urlsafe(48)
        expires_at = now + timedelta(days=self.settings.auth_refresh_token_expiry_days)
        self.db.add(
            RefreshToken(
                user_id=user_id,
                token_hash=self._hash_refresh_token(token),
                expires_at=expires_at,
            )
        )
        return token

    def _hash_refresh_token(self, refresh_token: str) -> str:
        return hmac.new(
            self.settings.auth_secret_key.encode("utf-8"),
            refresh_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _consume_open_otps(self, user_id: int, *, now: datetime) -> None:
        (
            self.db.query(EmailVerificationOTP)
            .filter(EmailVerificationOTP.user_id == user_id)
            .filter(EmailVerificationOTP.consumed_at.is_(None))
            .update({"consumed_at": now}, synchronize_session=False)
        )

    def _consume_open_password_reset_otps(self, user_id: int, *, now: datetime) -> None:
        (
            self.db.query(PasswordResetOTP)
            .filter(PasswordResetOTP.user_id == user_id)
            .filter(PasswordResetOTP.consumed_at.is_(None))
            .update({"consumed_at": now}, synchronize_session=False)
        )

    def _hash_otp_value(self, otp: str) -> str:
        return hmac.new(
            self.settings.auth_secret_key.encode("utf-8"),
            otp.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _verify_otp_value(self, otp: str, otp_hash: str) -> bool:
        cleaned = otp.strip()
        if not re.fullmatch(r"\d{6}", cleaned):
            return False
        return hmac.compare_digest(self._hash_otp_value(cleaned), otp_hash)

    def _create_access_token(self, payload: dict[str, str], *, expires_at: datetime) -> str:
        token_payload: dict[str, Any] = {
            **payload,
            "exp": int(expires_at.timestamp()),
            "iat": int(datetime.now(UTC).timestamp()),
            "typ": "access",
            "ver": TOKEN_VERSION,
        }
        payload_segment = _urlsafe_b64encode(
            json.dumps(token_payload, separators=(",", ":")).encode("utf-8")
        )
        signature = hmac.new(
            self.settings.auth_secret_key.encode("utf-8"),
            payload_segment.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{payload_segment}.{_urlsafe_b64encode(signature)}"

    def _decode_access_token(self, token: str) -> dict[str, Any]:
        try:
            payload_segment, signature_segment = token.split(".", 1)
            expected_signature = hmac.new(
                self.settings.auth_secret_key.encode("utf-8"),
                payload_segment.encode("ascii"),
                hashlib.sha256,
            ).digest()
            actual_signature = _urlsafe_b64decode(signature_segment)
        except (ValueError, UnicodeEncodeError, binascii.Error):
            raise AuthServiceError("Invalid access token.", status_code=401) from None

        if not hmac.compare_digest(actual_signature, expected_signature):
            raise AuthServiceError("Invalid access token.", status_code=401)

        try:
            payload = json.loads(_urlsafe_b64decode(payload_segment).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
            raise AuthServiceError("Invalid access token.", status_code=401) from None
        if payload.get("typ", "access") != "access" or payload.get("ver") != TOKEN_VERSION:
            raise AuthServiceError("Invalid access token.", status_code=401)
        if int(payload.get("exp", 0)) <= int(datetime.now(UTC).timestamp()):
            raise AuthServiceError("Access token has expired.", status_code=401)
        return payload

    def _send_otp_email(
        self,
        *,
        to_email: str,
        full_name: str,
        otp: str,
        subject: str = "Verify your Sales Automation account",
        intro: str = "Your verification OTP is below.",
    ) -> None:
        if not self.settings.email_host_user or not self.settings.email_host_password:
            raise EmailConfigurationError(
                "Email credentials are not configured. Set EMAIL_HOST_USER and "
                "EMAIL_HOST_PASSWORD in .env.",
                status_code=500,
            )

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((self.settings.email_from_name, self.settings.email_host_user))
        message["To"] = to_email
        message.set_content(
            "\n".join(
                [
                    f"Hi {full_name},",
                    "",
                    intro,
                    f"OTP: {otp}",
                    f"It expires in {self.settings.otp_expiry_minutes} minutes.",
                    "",
                    "If you did not request this account, you can ignore this email.",
                ]
            )
        )

        with smtplib.SMTP(self.settings.email_host, self.settings.email_port, timeout=15) as smtp:
            if self.settings.email_use_tls:
                smtp.starttls()
            smtp.login(self.settings.email_host_user, self.settings.email_host_password)
            smtp.send_message(message)


def _urlsafe_b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
