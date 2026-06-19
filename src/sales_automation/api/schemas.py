from __future__ import annotations

from datetime import datetime
from email.utils import parseaddr
import re
from typing import Any

from pydantic import BaseModel, Field
from pydantic import field_validator, model_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# ──────────────────────────────────────────────────────────────
# Database rows (for responses)
# ──────────────────────────────────────────────────────────────

class LeadRead(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str
    title: str | None
    company_name: str
    linkedin_url: str | None
    source: str | None
    status: str
    lead_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CompanyResearchRead(BaseModel):
    id: int
    lead_id: int
    company_domain: str | None
    website_url: str | None
    extracted_content: str | None
    summary: str
    pain_points: list[str | dict[str, Any]]
    signals: list[str | dict[str, Any]]
    sources: list[dict[str, Any]]
    model: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LeadScoreRead(BaseModel):
    id: int
    lead_id: int
    score: int
    grade: str
    rationale: str
    factors: dict[str, Any] | str
    model: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmailDraftRead(BaseModel):
    id: int
    lead_id: int
    subject: str
    body: str
    status: str
    model: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmailApprovalRead(BaseModel):
    id: int
    email_draft_id: int
    approved_by: str
    status: str
    notes: str | None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SentConversationMessageRead(BaseModel):
    id: int | str
    direction: str
    from_email: str | None
    to_email: str | None
    subject: str | None
    body: str
    sent_at: datetime
    reply_id: int | None = None
    gmail_message_id: str | None = None
    gmail_thread_id: str | None = None


class EmailReplyRead(BaseModel):
    id: int
    lead_id: int
    email_draft_id: int | None
    from_email: str
    sender_name: str | None = None
    company_name: str | None = None
    subject: str | None
    body: str
    preview: str
    sentiment: str | None
    status_label: str
    can_reply: bool
    date: datetime
    timestamp: datetime
    display_date: str
    received_at: datetime
    created_at: datetime
    updated_at: datetime
    original_subject: str | None = None
    original_body: str | None = None
    thread_body: str | None = None
    messages: list[SentConversationMessageRead] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class SentConversationRead(BaseModel):
    draft_id: int
    lead_id: int
    lead_name: str
    company_name: str
    recipient_email: str
    subject: str
    body: str
    original_body: str
    latest_body: str
    thread_body: str
    preview: str
    date: datetime
    timestamp: datetime
    display_date: str
    status_label: str
    sent_at: datetime
    message_count: int
    messages: list[SentConversationMessageRead]


class WorkflowLogRead(BaseModel):
    id: int
    lead_id: int | None
    event_type: str
    status: str
    message: str
    payload: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationLeadRead(BaseModel):
    id: int | None = None
    name: str | None = None
    email: str | None = None
    company_name: str | None = None


class NotificationThreadRead(BaseModel):
    reply_id: int | None = None
    draft_id: int | None = None
    gmail_message_id: str | None = None
    gmail_thread_id: str | None = None


class NotificationActionRead(BaseModel):
    label: str
    target: str
    method: str = "POST"


class NotificationItemRead(BaseModel):
    id: str
    type: str
    category: str
    status: str
    severity: str
    channel: str | None = None
    title: str
    message: str
    content: str
    preview: str
    badge_label: str
    badge_variant: str
    sender_email: str | None = None
    recipient_email: str | None = None
    subject: str | None = None
    timestamp: datetime
    timestamp_iso: str
    display_time: str
    display_date: str
    display_datetime: str
    lead: NotificationLeadRead | None = None
    thread: NotificationThreadRead | None = None
    action: NotificationActionRead | None = None
    reply_id: int | None = None
    draft_id: int | None = None
    gmail_message_id: str | None = None
    gmail_thread_id: str | None = None
    event_type: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class NotificationSummaryRead(BaseModel):
    total_alerts: int
    slack_feed_log: int
    hot_alerts: int
    replies_alert: int
    system_warnings: int


class NotificationCardRead(BaseModel):
    key: str
    label: str
    value: int
    display_value: str
    variant: str


class NotificationFilterRead(BaseModel):
    key: str
    label: str
    count: int
    active: bool = False


class NotificationListResult(BaseModel):
    status: str = "success"
    message: str = "Notifications fetched successfully."
    count: int
    summary: NotificationSummaryRead
    cards: list[NotificationCardRead]
    filters: list[NotificationFilterRead]
    notifications: list[NotificationItemRead]


class LeadDetailRead(BaseModel):
    lead: LeadRead
    research: CompanyResearchRead | None
    score: LeadScoreRead | None
    drafts: list[EmailDraftRead]
    replies: list[EmailReplyRead]
    approvals: list[EmailApprovalRead]


class UserRead(BaseModel):
    id: int
    full_name: str
    email: str
    is_active: bool
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ──────────────────────────────────────────────────────────────
# Request bodies
# ──────────────────────────────────────────────────────────────

class LeadImportRequest(BaseModel):
    source: str = Field(default="csv", description="Origin label for the import")


class ResearchRequest(BaseModel):
    limit: int = Field(
        default=100,
        ge=1,
        le=100,
        description="Number of new leads to research. API accepts up to 100 records per request.",
    )


class ScoreRequest(BaseModel):
    limit: int = Field(
        default=100,
        ge=1,
        le=100,
        description="Number of researched leads to score. API accepts up to 100 records per request.",
    )


class GenerateEmailRequest(BaseModel):
    categories: list[str] = Field(default_factory=lambda: ["HOT", "WARM"])
    limit: int = Field(default=100, ge=1, le=100)


class LeadListRequest(BaseModel):
    status: str | None = None
    limit: int = Field(default=100, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class LeadDetailRequest(BaseModel):
    lead_id: int


class DraftListRequest(BaseModel):
    lead_id: int | None = None
    status: str | None = None
    limit: int = Field(default=100, ge=1, le=100)


class ReplyListRequest(BaseModel):
    lead_id: int | None = None
    sentiment: str | None = None
    limit: int = Field(default=100, ge=1, le=100)


class SentListRequest(BaseModel):
    lead_id: int | None = None
    limit: int = Field(default=100, ge=1, le=100)


class WorkflowLogListRequest(BaseModel):
    lead_id: int | None = None
    event_type: str | None = None
    limit: int = Field(default=100, ge=1, le=100)


class NotificationListRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=100)
    include_slack: bool = True
    include_interested_replies: bool = True
    include_system_warnings: bool = True


class DraftActionRequest(BaseModel):
    action: str = Field(default="approve", description="approve, reject, or edit")
    approved_by: str | None = None
    notes: str | None = None
    subject: str | None = None
    body: str | None = None


class ClassifyRepliesRequest(BaseModel):
    query: str = Field(default="is:unread")
    limit: int = Field(default=100, ge=1, le=100)
    notify_slack: bool = Field(default=True)


class ReplyResponseRequest(BaseModel):
    body: str = Field(min_length=1)


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=180)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(max_length=128)
    confirm_password: str = Field(max_length=128)

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if len(cleaned) < 2:
            raise ValueError("Full name must be at least 2 characters.")
        return cleaned

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        parsed = parseaddr(value.strip())[1].lower()
        if not parsed or not EMAIL_RE.match(parsed):
            raise ValueError("Enter a valid email address.")
        return parsed

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters.")
        return value

    @field_validator("confirm_password")
    @classmethod
    def validate_confirm_password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Confirm password must be at least 8 characters.")
        return value

    @model_validator(mode="after")
    def validate_passwords_match(self) -> "RegisterRequest":
        if self.password != self.confirm_password:
            raise ValueError("Password and confirm password do not match.")
        return self


class VerifyOTPRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    otp: str = Field(min_length=6, max_length=6)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        parsed = parseaddr(value.strip())[1].lower()
        if not parsed or not EMAIL_RE.match(parsed):
            raise ValueError("Enter a valid email address.")
        return parsed

    @field_validator("otp")
    @classmethod
    def validate_otp(cls, value: str) -> str:
        cleaned = value.strip()
        if not re.fullmatch(r"\d{6}", cleaned):
            raise ValueError("OTP must be a 6-digit code.")
        return cleaned


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        parsed = parseaddr(value.strip())[1].lower()
        if not parsed or not EMAIL_RE.match(parsed):
            raise ValueError("Enter a valid email address.")
        return parsed


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        parsed = parseaddr(value.strip())[1].lower()
        if not parsed or not EMAIL_RE.match(parsed):
            raise ValueError("Enter a valid email address.")
        return parsed


class ResetPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    otp: str = Field(min_length=6, max_length=6)
    new_password: str = Field(max_length=128)
    confirm_password: str = Field(max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        parsed = parseaddr(value.strip())[1].lower()
        if not parsed or not EMAIL_RE.match(parsed):
            raise ValueError("Enter a valid email address.")
        return parsed

    @field_validator("otp")
    @classmethod
    def validate_otp(cls, value: str) -> str:
        cleaned = value.strip()
        if not re.fullmatch(r"\d{6}", cleaned):
            raise ValueError("OTP must be a 6-digit code.")
        return cleaned

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("New password must be at least 8 characters.")
        return value

    @field_validator("confirm_password")
    @classmethod
    def validate_confirm_password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Confirm password must be at least 8 characters.")
        return value

    @model_validator(mode="after")
    def validate_passwords_match(self) -> "ResetPasswordRequest":
        if self.new_password != self.confirm_password:
            raise ValueError("New password and confirm password do not match.")
        return self


# ──────────────────────────────────────────────────────────────
# Response wrappers
# ──────────────────────────────────────────────────────────────

class ImportResult(BaseModel):
    status: str = "success"
    message: str | None = None
    created: int
    updated: int
    skipped: int
    error_count: int = 0
    errors: list[dict[str, Any]]


class ResearchResult(BaseModel):
    status: str = "success"
    processed: int
    failed: int
    message: str | None = None


class ScoreResult(BaseModel):
    status: bool = True
    scored: int
    skipped: int
    failed: int
    message: str | None = None


class EmailGenResult(BaseModel):
    status: str = "success"
    generated: int
    skipped: int
    failed: int
    error_count: int
    errors: list[dict[str, Any]]
    message: str | None = None


class ReplyClassifyResult(BaseModel):
    status: str = "success"
    classified: int
    skipped: int
    failed: int
    error_count: int
    errors: list[dict[str, Any]]
    message: str | None = None


class ReplySendResult(BaseModel):
    status: str = "success"
    reply_id: int
    sent: bool
    message_id: str
    message: str | None = None


class AuthMessageResult(BaseModel):
    status: str = "success"
    message: str
    email: str


class AuthUserResult(BaseModel):
    status: str = "success"
    message: str
    user: UserRead


class LoginResult(BaseModel):
    status: str = "success"
    message: str
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserRead


class RefreshTokenResult(BaseModel):
    status: str = "success"
    message: str
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class SheetAppendResult(BaseModel):
    status: str = "success"
    message: str | None = None
    lead_id: int
    row: list[Any]
    column_count: int


class HealthCheck(BaseModel):
    status: str
    database: str
