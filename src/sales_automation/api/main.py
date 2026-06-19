from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from email.utils import parseaddr
from importlib import import_module
from pathlib import Path
from typing import Any

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import and_, exists, not_, or_, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from sales_automation.api.deps import get_current_user, get_db
from sales_automation.api.schemas import (
    AuthMessageResult,
    AuthUserResult,
    ClassifyRepliesRequest,
    DraftActionRequest,
    EmailDraftRead,
    EmailGenResult,
    EmailReplyRead,
    ForgotPasswordRequest,
    GenerateEmailRequest,
    HealthCheck,
    ImportResult,
    LeadDetailRead,
    LeadRead,
    LoginRequest,
    LoginResult,
    NotificationActionRead,
    NotificationCardRead,
    NotificationFilterRead,
    NotificationItemRead,
    NotificationLeadRead,
    NotificationListResult,
    NotificationSummaryRead,
    NotificationThreadRead,
    RefreshTokenRequest,
    RefreshTokenResult,
    RegisterRequest,
    ReplyClassifyResult,
    ReplyResponseRequest,
    ReplySendResult,
    ResearchRequest,
    ResearchResult,
    ResetPasswordRequest,
    ScoreRequest,
    ScoreResult,
    SentConversationMessageRead,
    SentConversationRead,
    SheetAppendResult,
    VerifyOTPRequest,
    WorkflowLogRead,
)
from sales_automation.config import get_settings
from sales_automation.db.session import SessionLocal
from sales_automation.models import Lead
from sales_automation.repositories import (
    CompanyResearchRepository,
    EmailApprovalRepository,
    EmailDraftRepository,
    EmailReplyRepository,
    EmailSentMessageRepository,
    LeadScoreRepository,
    WorkflowLogRepository,
)
from sales_automation.services.auth import AuthService, AuthServiceError

settings = get_settings()
MEDIA_DIR = Path("media")
logger = logging.getLogger(__name__)
AUTH_REQUIRED = [Depends(get_current_user)]
API_RECORD_LIMIT = 100
API_AI_MAX_SECONDS = 45


class _LazyFactory:
    def __init__(self, module_name: str, attribute_name: str) -> None:
        self.module_name = module_name
        self.attribute_name = attribute_name
        self._attribute: Any | None = None

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self._attribute is None:
            self._attribute = getattr(import_module(self.module_name), self.attribute_name)
        return self._attribute(*args, **kwargs)


LeadCSVImporter = _LazyFactory("sales_automation.services.csv_importer", "LeadCSVImporter")
EmailApprovalService = _LazyFactory(
    "sales_automation.services.email_approval", "EmailApprovalService"
)
ColdEmailGenerationService = _LazyFactory(
    "sales_automation.services.email_generation", "ColdEmailGenerationService"
)
GoogleSheetsLoggingService = _LazyFactory(
    "sales_automation.services.google_sheets_logging", "GoogleSheetsLoggingService"
)
AILeadScoringService = _LazyFactory(
    "sales_automation.services.lead_scoring", "AILeadScoringService"
)
OpenAIService = _LazyFactory("sales_automation.services.openai_service", "OpenAIService")
ReplyClassificationService = _LazyFactory(
    "sales_automation.services.reply_classification", "ReplyClassificationService"
)
SlackNotificationService = _LazyFactory(
    "sales_automation.services.slack_notification", "SlackNotificationService"
)
WebsiteResearchService = _LazyFactory(
    "sales_automation.services.website_research", "WebsiteResearchService"
)


def clean_reply_body(value: str) -> str:
    from sales_automation.services.gmail_service import clean_reply_body as _clean_reply_body

    return _clean_reply_body(value)


app = FastAPI(
    title="Sales Automation API",
    description="REST API for lead generation, AI scoring, email drafting, approvals, Gmail, Slack, and Sheets.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return _error_response(
        status_code=exc.status_code,
        message=_detail_message(exc.detail),
        errors=_detail_errors(exc.detail),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    errors = [
        {
            "field": _validation_field(error),
            "message": _validation_message(error),
            "type": str(error["type"]),
        }
        for error in exc.errors()
    ]
    return _error_response(status_code=422, message="Validation failed.", errors=errors)


def _validation_field(error: dict[str, Any]) -> str:
    parts = [str(part) for part in error["loc"] if str(part) != "body"]
    return ".".join(parts) or "request"


def _validation_message(error: dict[str, Any]) -> str:
    field = _validation_field(error)
    error_type = str(error["type"])
    message = str(error["msg"])
    if message.startswith("Value error, "):
        message = message.removeprefix("Value error, ")
    friendly_messages = {
        ("password", "string_too_short"): "Password must be at least 8 characters.",
        ("new_password", "string_too_short"): "New password must be at least 8 characters.",
        (
            "confirm_password",
            "string_too_short",
        ): "Confirm password must be at least 8 characters.",
    }
    return friendly_messages.get((field, error_type), message)


@app.exception_handler(SQLAlchemyError)
async def database_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.exception("api_database_error", extra={"path": request.url.path})
    return _error_response(
        status_code=500,
        message="Database operation failed. Please try again.",
        errors=[{"type": exc.__class__.__name__}],
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("api_unhandled_error", extra={"path": request.url.path})
    return _error_response(
        status_code=500,
        message="Internal server error. Please try again.",
        errors=[{"type": exc.__class__.__name__}],
    )


def _error_response(
    *,
    status_code: int,
    message: str,
    errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "error",
            "message": message,
            "errors": errors or [],
        },
    )


def _detail_message(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict) and detail.get("message"):
        return str(detail["message"])
    return "Request failed."


def _detail_errors(detail: Any) -> list[dict[str, Any]]:
    if isinstance(detail, dict):
        errors = detail.get("errors")
        if isinstance(errors, list):
            return [
                error if isinstance(error, dict) else {"message": str(error)} for error in errors
            ]
        return [detail]
    if isinstance(detail, list):
        return [error if isinstance(error, dict) else {"message": str(error)} for error in detail]
    return []


# ──────────────────────────────────────────────────────────────
# Health
# ──────────────────────────────────────────────────────────────


@app.get("/health", response_model=HealthCheck)
def health_check() -> HealthCheck:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"
    return HealthCheck(status="ok", database=db_status)


# ──────────────────────────────────────────────────────────────
# Authentication
# ──────────────────────────────────────────────────────────────


@app.post("/auth/register", response_model=AuthMessageResult)
def register_user(
    body: RegisterRequest,
    db: Session = Depends(get_db),
) -> AuthMessageResult:
    service = AuthService(db)
    try:
        user = service.register_user(
            full_name=body.full_name,
            email=body.email,
            password=body.password,
        )
        db.commit()
    except AuthServiceError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception:
        db.rollback()
        raise

    return AuthMessageResult(
        message="Registration successful. Please verify the OTP sent to your email.",
        email=user.email,
    )


@app.post("/auth/verify-otp", response_model=AuthUserResult)
def verify_registration_otp(
    body: VerifyOTPRequest,
    db: Session = Depends(get_db),
) -> AuthUserResult:
    service = AuthService(db)
    try:
        user = service.verify_otp(email=body.email, otp=body.otp)
        db.commit()
    except AuthServiceError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception:
        db.rollback()
        raise

    return AuthUserResult(message="Email verified successfully.", user=user)


@app.post("/auth/login", response_model=LoginResult)
def login_user(
    body: LoginRequest,
    db: Session = Depends(get_db),
) -> LoginResult:
    service = AuthService(db)
    try:
        user, access_token, refresh_token = service.login(email=body.email, password=body.password)
        db.commit()
    except AuthServiceError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception:
        db.rollback()
        raise

    return LoginResult(
        message="Login successful.",
        access_token=access_token,
        refresh_token=refresh_token,
        user=user,
    )


@app.post("/auth/refresh-token", response_model=RefreshTokenResult)
def refresh_token(
    body: RefreshTokenRequest,
    db: Session = Depends(get_db),
) -> RefreshTokenResult:
    service = AuthService(db)
    try:
        _, access_token, new_refresh_token = service.refresh_access_token(
            refresh_token=body.refresh_token
        )
        db.commit()
    except AuthServiceError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception:
        db.rollback()
        raise

    return RefreshTokenResult(
        message="Token refreshed successfully.",
        access_token=access_token,
        refresh_token=new_refresh_token,
    )


@app.post("/auth/forgot-password", response_model=AuthMessageResult)
def forgot_password(
    body: ForgotPasswordRequest,
    db: Session = Depends(get_db),
) -> AuthMessageResult:
    service = AuthService(db)
    try:
        email = service.request_password_reset(email=body.email)
        db.commit()
    except AuthServiceError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception:
        db.rollback()
        raise

    return AuthMessageResult(
        message="Password reset OTP sent to your email.",
        email=email,
    )


@app.post("/auth/reset-password", response_model=AuthUserResult)
def reset_password(
    body: ResetPasswordRequest,
    db: Session = Depends(get_db),
) -> AuthUserResult:
    service = AuthService(db)
    try:
        user = service.reset_password(
            email=body.email,
            otp=body.otp,
            new_password=body.new_password,
        )
        db.commit()
    except AuthServiceError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception:
        db.rollback()
        raise

    return AuthUserResult(message="Password reset successfully.", user=user)


# ──────────────────────────────────────────────────────────────
# Leads
# ──────────────────────────────────────────────────────────────


@app.get("/leads", response_model=list[LeadRead], dependencies=AUTH_REQUIRED)
def list_leads(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[LeadRead]:
    query = db.query(Lead)
    if status:
        query = query.filter(Lead.status == status)
    leads = query.order_by(Lead.created_at.desc()).limit(limit).offset(offset).all()
    return leads


@app.post("/leads/import", response_model=ImportResult, dependencies=AUTH_REQUIRED)
def import_leads(
    source: str = Form(default="csv"),
    file: UploadFile = File(...),
    research: bool = Form(default=False),
    research_limit: int = Form(default=100),
    db: Session = Depends(get_db),
) -> ImportResult:
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded CSV file is empty")
    file_path = _save_uploaded_csv(file.filename, content)

    importer = LeadCSVImporter(db)
    result = importer.import_file(file_path, source=source)
    db.commit()

    if research:
        research_service = WebsiteResearchService(db)
        research_service.research_pending_leads(limit=min(research_limit, API_RECORD_LIMIT))
        db.commit()

    error_count = len(result.errors)
    return ImportResult(
        status="success" if error_count == 0 else "partial_success",
        message=_import_message(result.created, result.updated, result.skipped, error_count),
        created=result.created,
        updated=result.updated,
        skipped=result.skipped,
        error_count=error_count,
        errors=result.errors,
    )


def _save_uploaded_csv(filename: str | None, content: bytes) -> Path:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    original_name = Path(filename or "leads.csv").name
    safe_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", original_name).strip(" ._")
    if not safe_name:
        safe_name = "leads.csv"
    if not safe_name.lower().endswith(".csv"):
        safe_name = f"{safe_name}.csv"
    path = MEDIA_DIR / f"{int(time.time())}_{safe_name}"
    path.write_bytes(content)
    return path


def _import_message(created: int, updated: int, skipped: int, error_count: int) -> str:
    if error_count:
        return f"Import completed with {error_count} errors."
    if created or updated:
        return "Leads imported successfully."
    if skipped:
        return "No new leads imported; rows were skipped."
    return "No leads imported."


@app.post("/leads/score", response_model=ScoreResult, dependencies=AUTH_REQUIRED)
def score_leads(
    body: ScoreRequest = ScoreRequest(),
    db: Session = Depends(get_db),
) -> ScoreResult:
    effective_limit = min(body.limit, API_RECORD_LIMIT)
    score_settings = settings.model_copy(
        update={
            "ai_timeout_seconds": min(settings.ai_timeout_seconds, 5),
            "ai_max_retries": 0,
        }
    )
    service = AILeadScoringService(db, openai_service=OpenAIService(settings=score_settings))
    result = service.score_unscored_leads(limit=effective_limit, max_seconds=API_AI_MAX_SECONDS)
    db.commit()
    return ScoreResult(
        status=result.status,
        scored=result.scored,
        skipped=result.skipped,
        failed=result.failed,
        message=result.message or _lead_scoring_message(result.scored, result.skipped, result.failed),
    )


def _lead_scoring_message(scored: int, skipped: int, failed: int) -> str:
    if scored > 0 and failed == 0:
        return "Lead scored."
    if scored > 0 and failed > 0:
        return "Lead scoring completed with some failures."
    if skipped > 0 and failed == 0:
        return "No new leads needed scoring."
    if failed > 0:
        return "Lead scoring failed."
    return "No researched leads available for scoring."


@app.get("/leads/{lead_id}", response_model=LeadDetailRead, dependencies=AUTH_REQUIRED)
def get_lead(lead_id: int, db: Session = Depends(get_db)) -> LeadDetailRead:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    research = (
        db.query(CompanyResearchRepository.model)
        .filter(CompanyResearchRepository.model.lead_id == lead_id)
        .order_by(CompanyResearchRepository.model.created_at.desc())
        .first()
    )
    score = (
        db.query(LeadScoreRepository.model)
        .filter(LeadScoreRepository.model.lead_id == lead_id)
        .order_by(LeadScoreRepository.model.created_at.desc())
        .first()
    )
    drafts = (
        db.query(EmailDraftRepository.model)
        .filter(EmailDraftRepository.model.lead_id == lead_id)
        .order_by(EmailDraftRepository.model.created_at.desc())
        .all()
    )
    replies = (
        db.query(EmailReplyRepository.model)
        .filter(EmailReplyRepository.model.lead_id == lead_id)
        .order_by(EmailReplyRepository.model.received_at.desc())
        .all()
    )
    draft_ids = [draft.id for draft in drafts]
    approvals: list[Any] = (
        (
            db.query(EmailApprovalRepository.model)
            .filter(EmailApprovalRepository.model.email_draft_id.in_(draft_ids))
            .all()
        )
        if draft_ids
        else []
    )

    return LeadDetailRead(
        lead=lead,
        research=research,
        score=score,
        drafts=drafts,
        replies=[_reply_to_read(reply) for reply in replies],
        approvals=approvals,
    )


# ──────────────────────────────────────────────────────────────
# Website Research
# ──────────────────────────────────────────────────────────────


@app.post("/research/websites", response_model=ResearchResult, dependencies=AUTH_REQUIRED)
def research_websites(
    body: ResearchRequest = ResearchRequest(),
    db: Session = Depends(get_db),
) -> ResearchResult:
    effective_limit = min(body.limit, API_RECORD_LIMIT)
    research_settings = settings.model_copy(
        update={
            "http_timeout_seconds": min(settings.http_timeout_seconds, 4),
            "http_max_retries": 1,
            "ai_timeout_seconds": min(settings.ai_timeout_seconds, 5),
            "ai_max_retries": 0,
            "website_max_content_chars": min(settings.website_max_content_chars, 4_000),
        }
    )
    service = WebsiteResearchService(db, settings=research_settings, ai_fallback_enabled=False)
    result = service.research_pending_leads(limit=effective_limit, max_seconds=API_AI_MAX_SECONDS)
    db.commit()
    message = _website_research_message(
        requested_limit=body.limit,
        effective_limit=effective_limit,
        processed=int(result["processed"]),
        failed=int(result["failed"]),
        timed_out=bool(result.get("timed_out")),
    )
    return ResearchResult(
        status="success" if result["failed"] == 0 else "partial_success",
        processed=result["processed"],
        failed=result["failed"],
        message=message,
    )


def _website_research_message(
    *,
    requested_limit: int,
    effective_limit: int,
    processed: int,
    failed: int,
    timed_out: bool,
) -> str:
    if failed:
        return f"Website research completed with {failed} failed lead(s)."
    if timed_out:
        return "Website research stopped at the API time limit. Call this endpoint again for more leads."
    if effective_limit < requested_limit:
        return (
            f"Website research completed for {effective_limit} leads. "
            "Call this endpoint again to process the next batch."
        )
    if processed:
        return "Website research completed."
    return "No pending leads available for website research."


# ──────────────────────────────────────────────────────────────
# Email Generation
# ──────────────────────────────────────────────────────────────


def _email_generation_message(db: Session, categories: list[str]) -> str:
    scored = (
        db.query(Lead)
        .join(LeadScoreRepository.model)
        .filter(LeadScoreRepository.model.grade.in_(categories))
        .count()
    )
    with_research = (
        db.query(Lead)
        .join(LeadScoreRepository.model)
        .filter(LeadScoreRepository.model.grade.in_(categories))
        .filter(exists().where(CompanyResearchRepository.model.lead_id == Lead.id))
        .count()
    )
    existing_drafts = (
        db.query(Lead)
        .join(LeadScoreRepository.model)
        .filter(LeadScoreRepository.model.grade.in_(categories))
        .filter(exists().where(CompanyResearchRepository.model.lead_id == Lead.id))
        .filter(exists().where(EmailDraftRepository.model.lead_id == Lead.id))
        .count()
    )
    available = max(with_research - existing_drafts, 0)

    if available:
        return "Email generation completed."
    if scored == 0:
        return "No HOT or WARM scored leads found. Score leads first or include COLD in categories."
    if with_research == 0:
        return "No scored leads with company research found. Run website research first."
    if existing_drafts >= with_research:
        return "All qualified leads already have email drafts."
    return "No qualified leads available for email generation."


@app.post("/emails/generate", response_model=EmailGenResult, dependencies=AUTH_REQUIRED)
def generate_emails(
    body: GenerateEmailRequest = GenerateEmailRequest(),
    db: Session = Depends(get_db),
) -> EmailGenResult:
    service = ColdEmailGenerationService(db)
    result = service.generate_emails_for_qualified_leads(
        categories=body.categories,
        limit=min(body.limit, API_RECORD_LIMIT),
        notify_slack=False,
        max_seconds=API_AI_MAX_SECONDS,
    )
    db.commit()
    message = "Email generation completed."
    if result.generated == 0 and result.failed == 0:
        message = _email_generation_message(db, body.categories)
    return EmailGenResult(
        status="success" if result.failed == 0 else "partial_success",
        generated=result.generated,
        skipped=result.skipped,
        failed=result.failed,
        error_count=len(result.errors),
        errors=result.errors,
        message=message,
    )


# ──────────────────────────────────────────────────────────────
# Draft Approvals
# ──────────────────────────────────────────────────────────────


@app.get("/drafts/pending", dependencies=AUTH_REQUIRED)
def list_pending_drafts(
    limit: int = Query(default=100, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    service = EmailApprovalService(db)
    drafts = service.list_pending(limit=limit)
    return [
        {
            "id": d.id,
            "lead_id": d.lead_id,
            "subject": d.subject,
            "body": d.body,
            "status": d.status,
        }
        for d in drafts
    ]


def _log_sheet_after_send(session_factory: Any, lead_id: int) -> None:
    try:
        with session_factory() as db:
            GoogleSheetsLoggingService(db).append_lead_row(lead_id)
            db.commit()
    except Exception as exc:
        logger = logging.getLogger(__name__)
        logger.exception(
            "google_sheets_auto_log_failed", extra={"lead_id": lead_id, "error": str(exc)}
        )


@app.put("/drafts/{draft_id}", response_model=dict[str, Any], dependencies=AUTH_REQUIRED)
def draft_action(
    draft_id: int,
    background_tasks: BackgroundTasks,
    body: DraftActionRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    service = EmailApprovalService(db)
    action = body.action.lower()

    try:
        if action == "approve":
            draft = service.approve(
                draft_id,
                approved_by=body.approved_by or "sales",
                notes=body.notes,
            )
            db.commit()
            sent_draft = service.send_approved(draft.id)
            db.commit()
            background_tasks.add_task(_log_sheet_after_send, SessionLocal, sent_draft.lead_id)
            return {
                "api_status": "success",
                "message": "Draft approved and email sent.",
                "draft_id": sent_draft.id,
                "status": sent_draft.status,
                "sent": True,
                "sheet_update_queued": True,
            }
        if action == "reject":
            draft = service.reject(
                draft_id,
                approved_by=body.approved_by or "sales",
                notes=body.notes,
            )
            db.commit()
            return {
                "api_status": "success",
                "message": "Draft rejected.",
                "draft_id": draft.id,
                "status": draft.status,
                "sent": False,
            }
        if action == "edit":
            draft = service.edit(draft_id, subject=body.subject, body=body.body)
            db.commit()
            return {
                "api_status": "success",
                "message": "Draft edited and saved for approval.",
                "draft_id": draft.id,
                "status": draft.status,
                "sent": False,
                "sheet_update_queued": False,
            }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    raise HTTPException(status_code=400, detail="Invalid action. Use: approve, reject, or edit")


@app.get("/drafts", response_model=list[EmailDraftRead], dependencies=AUTH_REQUIRED)
def list_drafts(
    lead_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[EmailDraftRead]:
    query = db.query(EmailDraftRepository.model)
    if lead_id:
        query = query.filter(EmailDraftRepository.model.lead_id == lead_id)
    if status:
        query = query.filter(EmailDraftRepository.model.status == status)
    return query.order_by(EmailDraftRepository.model.created_at.desc()).limit(limit).all()


# ──────────────────────────────────────────────────────────────
# Sent Conversations
# ──────────────────────────────────────────────────────────────


@app.get("/sent", response_model=list[SentConversationRead], dependencies=AUTH_REQUIRED)
def list_sent_conversations(
    lead_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[SentConversationRead]:
    query = db.query(EmailDraftRepository.model).filter(EmailDraftRepository.model.status == "sent")
    if lead_id:
        query = query.filter(EmailDraftRepository.model.lead_id == lead_id)
    drafts = query.order_by(EmailDraftRepository.model.updated_at.desc()).limit(limit).all()
    if not drafts:
        return []

    draft_ids = [draft.id for draft in drafts]
    replies_by_draft = _group_by_email_draft_id(
        db.query(EmailReplyRepository.model)
        .filter(EmailReplyRepository.model.email_draft_id.in_(draft_ids))
        .order_by(EmailReplyRepository.model.received_at.asc())
        .all()
    )
    sent_by_draft = _group_by_email_draft_id(
        db.query(EmailSentMessageRepository.model)
        .filter(EmailSentMessageRepository.model.email_draft_id.in_(draft_ids))
        .order_by(
            EmailSentMessageRepository.model.sent_at.asc(),
            EmailSentMessageRepository.model.id.asc(),
        )
        .all()
    )
    conversations = [
        _sent_conversation_for_draft(
            db,
            draft,
            replies=replies_by_draft.get(draft.id, []),
            sent_messages=sent_by_draft.get(draft.id, []),
        )
        for draft in drafts
    ]
    return sorted(conversations, key=lambda item: item.sent_at, reverse=True)


def _sent_conversation_for_draft(
    db: Session,
    draft: Any,
    *,
    replies: list[Any] | None = None,
    sent_messages: list[Any] | None = None,
) -> SentConversationRead:
    lead = draft.lead
    draft_sent_at = _safe_datetime(
        getattr(draft, "updated_at", None), getattr(draft, "created_at", None)
    )
    messages: list[SentConversationMessageRead] = [
        SentConversationMessageRead(
            id=f"draft-{draft.id}",
            direction="outbound",
            from_email=settings.gmail_sender_email,
            to_email=lead.email,
            subject=draft.subject,
            body=draft.body,
            sent_at=draft_sent_at,
            reply_id=None,
        )
    ]
    messages.extend(
        SentConversationMessageRead(
            id=f"reply-{reply.id}",
            direction="inbound",
            from_email=reply.from_email,
            to_email=settings.gmail_sender_email,
            subject=reply.subject,
            body=clean_reply_body(reply.body),
            sent_at=_safe_datetime(
                getattr(reply, "received_at", None),
                getattr(reply, "updated_at", None),
                getattr(reply, "created_at", None),
            ),
            reply_id=reply.id,
            gmail_message_id=reply.gmail_message_id,
            gmail_thread_id=reply.gmail_thread_id,
        )
        for reply in (
            replies if replies is not None else EmailReplyRepository(db).list_for_draft(draft.id)
        )
    )
    messages.extend(
        SentConversationMessageRead(
            id=f"sent-{message.id}",
            direction="outbound",
            from_email=settings.gmail_sender_email,
            to_email=message.to_email,
            subject=message.subject,
            body=message.body,
            sent_at=_safe_datetime(
                getattr(message, "sent_at", None),
                getattr(message, "updated_at", None),
                getattr(message, "created_at", None),
            ),
            reply_id=message.email_reply_id,
            gmail_message_id=message.gmail_message_id,
            gmail_thread_id=message.gmail_thread_id,
        )
        for message in (
            sent_messages
            if sent_messages is not None
            else EmailSentMessageRepository(db).list_for_draft(draft.id)
        )
    )
    messages = sorted(messages, key=lambda message: message.sent_at)
    latest = messages[-1]
    lead_name = f"{lead.first_name} {lead.last_name}".strip()
    thread_body = _thread_body(messages)
    return SentConversationRead(
        draft_id=draft.id,
        lead_id=draft.lead_id,
        lead_name=lead_name or lead.email,
        company_name=lead.company_name,
        recipient_email=lead.email,
        subject=draft.subject,
        body=draft.body,
        original_body=draft.body,
        latest_body=latest.body,
        thread_body=thread_body,
        preview=latest.body,
        date=latest.sent_at,
        timestamp=latest.sent_at,
        display_date=_display_date(latest.sent_at),
        status_label="Sent",
        sent_at=latest.sent_at,
        message_count=len(messages),
        messages=messages,
    )


def _group_by_email_draft_id(items: list[Any]) -> dict[int, list[Any]]:
    grouped: dict[int, list[Any]] = {}
    for item in items:
        draft_id = getattr(item, "email_draft_id", None)
        if draft_id is None:
            continue
        grouped.setdefault(draft_id, []).append(item)
    return grouped


# ──────────────────────────────────────────────────────────────
# Gmail / Replies
# ──────────────────────────────────────────────────────────────


@app.post("/replies/classify", response_model=ReplyClassifyResult, dependencies=AUTH_REQUIRED)
def classify_replies(
    body: ClassifyRepliesRequest = ClassifyRepliesRequest(),
    db: Session = Depends(get_db),
) -> ReplyClassifyResult:
    service = ReplyClassificationService(db)
    result = service.classify_gmail_replies(
        query=body.query,
        limit=min(body.limit, API_RECORD_LIMIT),
        notify_slack=body.notify_slack,
        max_seconds=API_AI_MAX_SECONDS,
    )
    db.commit()
    return ReplyClassifyResult(
        status="success" if not result.errors else "partial_success",
        classified=result.classified,
        skipped=result.skipped,
        failed=result.failed,
        error_count=len(result.errors),
        errors=result.errors,
        message=_reply_classification_message(result.classified, result.skipped, result.failed),
    )


@app.post(
    "/replies/classify-unclassified",
    response_model=ReplyClassifyResult,
    dependencies=AUTH_REQUIRED,
)
def classify_unclassified_replies(
    limit: int = Query(default=100, ge=1, le=100),
    notify_slack: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> ReplyClassifyResult:
    service = ReplyClassificationService(db)
    result = service.classify_unclassified_replies(
        limit=min(limit, API_RECORD_LIMIT),
        notify_slack=notify_slack,
        max_seconds=API_AI_MAX_SECONDS,
    )
    db.commit()
    return ReplyClassifyResult(
        status="success" if not result.errors else "partial_success",
        classified=result.classified,
        skipped=result.skipped,
        failed=result.failed,
        error_count=len(result.errors),
        errors=result.errors,
        message=_reply_classification_message(result.classified, result.skipped, result.failed),
    )


def _reply_classification_message(classified: int, skipped: int, failed: int) -> str:
    if classified and not failed:
        return "Replies classified successfully."
    if classified and failed:
        return "Replies classified with some failures."
    if skipped and not failed:
        return "No matching replies found to classify."
    if failed:
        return "Reply classification failed."
    return "No replies available for classification."


@app.get("/replies", response_model=list[EmailReplyRead], dependencies=AUTH_REQUIRED)
def list_replies(
    lead_id: int | None = Query(default=None),
    sentiment: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[EmailReplyRead]:
    query = db.query(EmailReplyRepository.model)
    if lead_id:
        query = query.filter(EmailReplyRepository.model.lead_id == lead_id)
    if sentiment:
        requested_sentiment = _canonical_reply_sentiment(sentiment)
        if requested_sentiment == "INTERESTED":
            query = query.filter(
                or_(
                    and_(
                        EmailReplyRepository.model.sentiment.in_(_sentiment_variants("INTERESTED")),
                        not_(_negative_reply_body_filter(EmailReplyRepository.model)),
                    ),
                    _positive_reply_body_filter(EmailReplyRepository.model),
                )
            )
        elif requested_sentiment == "NOT_INTERESTED":
            query = query.filter(
                or_(
                    and_(
                        EmailReplyRepository.model.sentiment.in_(
                            _sentiment_variants("NOT_INTERESTED")
                        ),
                        not_(_positive_reply_body_filter(EmailReplyRepository.model)),
                    ),
                    _negative_reply_body_filter(EmailReplyRepository.model),
                )
            )
        else:
            query = query.filter(EmailReplyRepository.model.sentiment == sentiment)
    replies = query.order_by(EmailReplyRepository.model.received_at.desc()).limit(limit).all()
    response = [_reply_to_read(reply) for reply in replies]
    if _repair_reply_sentiments(replies, response):
        db.commit()
    return response


def _reply_to_read(reply: Any) -> EmailReplyRead:
    messages = _reply_thread_messages(reply)
    body = clean_reply_body(reply.body)
    sentiment = _effective_reply_sentiment(getattr(reply, "sentiment", None), body)
    received_at = _safe_datetime(
        getattr(reply, "received_at", None),
        getattr(reply, "updated_at", None),
        getattr(reply, "created_at", None),
    )
    created_at = _safe_datetime(getattr(reply, "created_at", None), received_at)
    updated_at = _safe_datetime(getattr(reply, "updated_at", None), received_at)
    return EmailReplyRead(
        id=reply.id,
        lead_id=reply.lead_id,
        email_draft_id=reply.email_draft_id,
        from_email=reply.from_email,
        sender_name=_display_sender(reply),
        company_name=reply.lead.company_name if getattr(reply, "lead", None) else None,
        subject=reply.subject,
        body=body,
        preview=body,
        sentiment=sentiment,
        status_label=_reply_status_label(sentiment),
        can_reply=bool(getattr(reply, "from_email", None)),
        date=received_at,
        timestamp=received_at,
        display_date=_display_date(received_at),
        received_at=received_at,
        created_at=created_at,
        updated_at=updated_at,
        original_subject=reply.email_draft.subject if reply.email_draft else None,
        original_body=reply.email_draft.body if reply.email_draft else None,
        thread_body=_thread_body(messages) if messages else None,
        messages=messages,
    )


def _reply_thread_messages(reply: Any) -> list[SentConversationMessageRead]:
    messages: list[SentConversationMessageRead] = []
    if reply.email_draft:
        draft_sent_at = _safe_datetime(
            getattr(reply.email_draft, "updated_at", None),
            getattr(reply.email_draft, "created_at", None),
        )
        messages.append(
            SentConversationMessageRead(
                id=f"draft-{reply.email_draft.id}",
                direction="outbound",
                from_email=settings.gmail_sender_email,
                to_email=reply.lead.email if reply.lead else None,
                subject=reply.email_draft.subject,
                body=reply.email_draft.body,
                sent_at=draft_sent_at,
                reply_id=None,
            )
        )
    reply_received_at = _safe_datetime(
        getattr(reply, "received_at", None),
        getattr(reply, "updated_at", None),
        getattr(reply, "created_at", None),
    )
    messages.append(
        SentConversationMessageRead(
            id=f"reply-{reply.id}",
            direction="inbound",
            from_email=reply.from_email,
            to_email=settings.gmail_sender_email,
            subject=reply.subject,
            body=clean_reply_body(reply.body),
            sent_at=reply_received_at,
            reply_id=reply.id,
            gmail_message_id=getattr(reply, "gmail_message_id", None),
            gmail_thread_id=getattr(reply, "gmail_thread_id", None),
        )
    )
    messages.extend(
        SentConversationMessageRead(
            id=f"sent-{message.id}",
            direction="outbound",
            from_email=settings.gmail_sender_email,
            to_email=message.to_email,
            subject=message.subject,
            body=message.body,
            sent_at=_safe_datetime(
                getattr(message, "sent_at", None),
                getattr(message, "updated_at", None),
                getattr(message, "created_at", None),
                reply_received_at,
            ),
            reply_id=message.email_reply_id,
            gmail_message_id=message.gmail_message_id,
            gmail_thread_id=message.gmail_thread_id,
        )
        for message in getattr(reply, "sent_messages", [])
    )
    return sorted(messages, key=lambda message: message.sent_at)


def _thread_body(messages: list[SentConversationMessageRead]) -> str:
    return "\n\n".join(
        f"{message.direction.upper()} {message.sent_at.isoformat()}\n"
        f"Subject: {message.subject or '(no subject)'}\n\n"
        f"{message.body}"
        for message in messages
    )


def _display_sender(reply: Any) -> str:
    parsed_name, parsed_email = parseaddr(reply.from_email or "")
    if parsed_name:
        return parsed_name
    if getattr(reply, "lead", None):
        lead_name = f"{reply.lead.first_name} {reply.lead.last_name}".strip()
        if lead_name:
            return lead_name
    return parsed_email or reply.from_email or "Unknown sender"


def _reply_status_label(sentiment: str | None) -> str:
    labels = {
        "INTERESTED": "Interested",
        "NOT_INTERESTED": "Not Interested",
        "NEEDS_FOLLOW_UP": "Follow Up",
        "OUT_OF_OFFICE": "Out of Office",
        "SPAM": "Spam",
    }
    return labels.get(_canonical_reply_sentiment(sentiment) or "", "Unclassified")


def _effective_reply_sentiment(sentiment: str | None, body: str) -> str | None:
    current = _canonical_reply_sentiment(sentiment)
    try:
        from sales_automation.services.reply_classification import classify_obvious_reply_intent
    except ImportError:
        return current

    obvious = classify_obvious_reply_intent(body)
    if obvious is None:
        return current
    obvious_label = str(obvious["classification"])
    if obvious_label in {"INTERESTED", "NOT_INTERESTED"} and obvious_label != current:
        return obvious_label
    if current is None:
        return obvious_label
    return current


def _canonical_reply_sentiment(sentiment: str | None) -> str | None:
    if sentiment is None:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(sentiment).strip()).strip("_").upper()
    aliases = {
        "INTERESTED": "INTERESTED",
        "NOT_INTERESTED": "NOT_INTERESTED",
        "NOTINTERESTED": "NOT_INTERESTED",
        "NO_INTEREST": "NOT_INTERESTED",
        "OUT_OF_OFFICE": "OUT_OF_OFFICE",
        "OOO": "OUT_OF_OFFICE",
        "NEEDS_FOLLOW_UP": "NEEDS_FOLLOW_UP",
        "FOLLOW_UP": "NEEDS_FOLLOW_UP",
        "SPAM": "SPAM",
    }
    return aliases.get(cleaned, cleaned or None)


def _repair_reply_sentiments(replies: list[Any], response: list[EmailReplyRead]) -> bool:
    changed = False
    for reply, read in zip(replies, response, strict=False):
        stored = _canonical_reply_sentiment(getattr(reply, "sentiment", None))
        if read.sentiment and read.sentiment != stored:
            reply.sentiment = read.sentiment
            changed = True
    return changed


def _sentiment_variants(sentiment: str) -> list[str]:
    if sentiment == "INTERESTED":
        return ["INTERESTED", "Interested", "interested"]
    if sentiment == "NOT_INTERESTED":
        return [
            "NOT_INTERESTED",
            "Not Interested",
            "not interested",
            "not_interested",
            "NOT INTERESTED",
        ]
    return [sentiment]


def _positive_reply_body_filter(reply_model: Any) -> Any:
    return and_(
        not_(_negative_reply_body_filter(reply_model)),
        or_(
            reply_model.body.ilike("%interested%"),
            reply_model.body.ilike("%yes%"),
            reply_model.body.ilike("%sounds good%"),
            reply_model.body.ilike("%schedule%"),
            reply_model.body.ilike("%meeting%"),
            reply_model.body.ilike("%call%"),
            reply_model.body.ilike("%tell me more%"),
            reply_model.body.ilike("%happy to%"),
        ),
    )


def _negative_reply_body_filter(reply_model: Any) -> Any:
    return or_(
        reply_model.body.ilike("%not interested%"),
        reply_model.body.ilike("%no thanks%"),
        reply_model.body.ilike("%no thank you%"),
        reply_model.body.ilike("%no interest%"),
        reply_model.body.ilike("%not a fit%"),
        reply_model.body.ilike("%unsubscribe%"),
        reply_model.body.ilike("%remove me%"),
    )


def _display_date(value: Any) -> str:
    value = _safe_datetime(value)
    try:
        return value.strftime("%b %-d, %Y, %-I:%M %p")
    except ValueError:
        return value.strftime("%b %d, %Y, %I:%M %p")


def _display_time(value: Any) -> str:
    value = _safe_datetime(value)
    try:
        return value.strftime("%-I:%M %p")
    except ValueError:
        return value.strftime("%I:%M %p")


def _display_datetime(value: Any) -> str:
    value = _safe_datetime(value)
    try:
        return value.strftime("%d/%m/%Y, %H:%M")
    except ValueError:
        return value.strftime("%d/%m/%Y, %H:%M")


def _safe_datetime(value: Any, *fallbacks: Any) -> datetime:
    for candidate in (value, *fallbacks):
        parsed = _parse_datetime(candidate)
        if parsed is not None:
            return parsed
    return datetime.now(UTC)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_timezone(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        if cleaned.endswith("Z"):
            cleaned = f"{cleaned[:-1]}+00:00"
        try:
            return _ensure_timezone(datetime.fromisoformat(cleaned))
        except ValueError:
            logger.warning("invalid_datetime_value", extra={"value": value})
            return None
    return None


def _ensure_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@app.post("/replies/{reply_id}/respond", response_model=ReplySendResult, dependencies=AUTH_REQUIRED)
def respond_to_reply(
    reply_id: int,
    body: ReplyResponseRequest,
    db: Session = Depends(get_db),
) -> ReplySendResult:
    service = ReplyClassificationService(db)
    try:
        message_id = service.send_response_to_reply(reply_id, body=body.body)
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    return ReplySendResult(
        status="success",
        reply_id=reply_id,
        sent=True,
        message_id=message_id,
        message="Response sent successfully.",
    )


# ──────────────────────────────────────────────────────────────
# Google Sheets Logging
# ──────────────────────────────────────────────────────────────


@app.post("/sheets/log/{lead_id}", response_model=SheetAppendResult, dependencies=AUTH_REQUIRED)
def log_lead_to_sheet(lead_id: int, db: Session = Depends(get_db)) -> SheetAppendResult:
    service = GoogleSheetsLoggingService(db)
    try:
        row = service.append_lead_row(lead_id)
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SheetAppendResult(
        status="success",
        message="Lead row appended to Google Sheets.",
        lead_id=lead_id,
        row=row,
        column_count=len(row),
    )


# ──────────────────────────────────────────────────────────────
# Slack Notifications
# ──────────────────────────────────────────────────────────────


@app.post("/notify/slack/test", dependencies=AUTH_REQUIRED)
def test_slack_notification() -> dict[str, str]:
    slack = SlackNotificationService()
    from sales_automation.models import Lead, LeadScore

    lead = Lead(
        id=1,
        first_name="John",
        last_name="Smith",
        email="john@techcorp.com",
        company_name="TechCorp",
    )
    score = LeadScore(id=1, lead_id=1, score=5, grade="HOT", rationale="Test notification")
    try:
        slack.notify_hot_lead(lead=lead, score=score)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Store successful manual test notifications so the notifications API reflects them.
    with SessionLocal() as db:
        WorkflowLogRepository(db).record(
            lead_id=None,
            event_type="slack.test_notification_sent",
            status="completed",
            message="Slack test notification delivered.",
            payload={"company_name": lead.company_name, "score": score.score, "grade": score.grade},
        )
        db.commit()
    return {"status": "success", "message": "Slack notification sent."}


# ──────────────────────────────────────────────────────────────
# Notifications
# ──────────────────────────────────────────────────────────────


@app.get("/notifications", response_model=NotificationListResult, dependencies=AUTH_REQUIRED)
def list_notifications(
    limit: int = Query(default=100, ge=1, le=100),
    include_slack: bool = Query(default=True),
    include_interested_replies: bool = Query(default=True),
    include_system_warnings: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> NotificationListResult:
    limit = min(limit, API_RECORD_LIMIT)
    notifications: list[NotificationItemRead] = []
    if include_slack:
        notifications.extend(_slack_notification_items(db, limit=limit))
    if include_interested_replies:
        notifications.extend(_interested_reply_items(db, limit=limit))
    if include_system_warnings:
        notifications.extend(_system_warning_items(db, limit=limit))

    notifications = sorted(
        notifications,
        key=lambda item: item.timestamp,
        reverse=True,
    )[:limit]
    return NotificationListResult(
        count=len(notifications),
        summary=_notification_summary(notifications),
        cards=_notification_cards(notifications),
        filters=_notification_filters(notifications),
        notifications=notifications,
    )


def _slack_notification_items(db: Session, *, limit: int) -> list[NotificationItemRead]:
    logs = (
        db.query(WorkflowLogRepository.model)
        .options(joinedload(WorkflowLogRepository.model.lead))
        .filter(WorkflowLogRepository.model.event_type.like("slack.%"))
        .filter(WorkflowLogRepository.model.status == "completed")
        .order_by(WorkflowLogRepository.model.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_slack_log_to_notification(log) for log in logs]


def _interested_reply_items(db: Session, *, limit: int) -> list[NotificationItemRead]:
    reply_model = EmailReplyRepository.model
    positive_body_filter = _positive_reply_body_filter(reply_model)
    replies = (
        db.query(reply_model)
        .options(joinedload(reply_model.lead), joinedload(reply_model.email_draft))
        .filter(
            or_(
                and_(
                    reply_model.sentiment.in_(_sentiment_variants("INTERESTED")),
                    not_(_negative_reply_body_filter(reply_model)),
                ),
                positive_body_filter,
            )
        )
        .order_by(reply_model.received_at.desc())
        .limit(limit)
        .all()
    )
    return [_interested_reply_to_notification(reply) for reply in replies]


def _system_warning_items(db: Session, *, limit: int) -> list[NotificationItemRead]:
    logs = (
        db.query(WorkflowLogRepository.model)
        .options(joinedload(WorkflowLogRepository.model.lead))
        .filter(WorkflowLogRepository.model.status.in_(("failed", "warning")))
        .order_by(WorkflowLogRepository.model.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_workflow_warning_to_notification(log) for log in logs]


def _slack_log_to_notification(log: Any) -> NotificationItemRead:
    timestamp = _safe_datetime(getattr(log, "created_at", None))
    payload = dict(getattr(log, "payload", None) or {})
    lead = getattr(log, "lead", None)
    event_type = getattr(log, "event_type", "") or ""
    category = _notification_category(event_type=event_type, item_type="slack_notification")
    thread = _notification_thread(
        reply_id=_payload_int(payload, "reply_id"),
        draft_id=_payload_int(payload, "draft_id"),
        gmail_message_id=payload.get("message_id"),
        gmail_thread_id=payload.get("gmail_thread_id"),
    )
    title = _notification_title(
        category=category,
        event_type=event_type,
        lead=_notification_lead(lead),
        payload=payload,
    )
    content = getattr(log, "message", "") or title
    return NotificationItemRead(
        id=f"slack-log-{log.id}",
        type="slack_notification",
        category=category,
        status=getattr(log, "status", "completed"),
        severity=_notification_severity(category=category, status=getattr(log, "status", None)),
        channel=str(payload.get("channel") or "#sales-alerts"),
        title=title,
        message=title,
        content=content,
        preview=_notification_preview(content),
        badge_label=_notification_badge_label(category),
        badge_variant=_notification_badge_variant(category),
        sender_email=None,
        recipient_email=None,
        subject=None,
        timestamp=timestamp,
        timestamp_iso=timestamp.isoformat(),
        display_time=_display_time(timestamp),
        display_date=_display_date(timestamp),
        display_datetime=_display_datetime(timestamp),
        lead=_notification_lead(lead),
        thread=thread,
        action=_notification_action(category=category, thread=thread),
        reply_id=thread.reply_id,
        draft_id=thread.draft_id,
        gmail_message_id=thread.gmail_message_id,
        gmail_thread_id=thread.gmail_thread_id,
        event_type=event_type,
        payload=payload,
    )


def _interested_reply_to_notification(reply: Any) -> NotificationItemRead:
    received_at = _safe_datetime(
        getattr(reply, "received_at", None),
        getattr(reply, "updated_at", None),
        getattr(reply, "created_at", None),
    )
    body = clean_reply_body(getattr(reply, "body", "") or "")
    sentiment = _effective_reply_sentiment(getattr(reply, "sentiment", None), body)
    lead = _notification_lead(getattr(reply, "lead", None))
    thread = _notification_thread(
        reply_id=getattr(reply, "id", None),
        draft_id=getattr(reply, "email_draft_id", None),
        gmail_message_id=getattr(reply, "gmail_message_id", None),
        gmail_thread_id=getattr(reply, "gmail_thread_id", None),
    )
    title = _notification_title(
        category="interested_reply",
        event_type="email_reply.interested",
        lead=lead,
        payload={
            "subject": getattr(reply, "subject", None),
            "sender_email": getattr(reply, "from_email", None),
        },
    )
    return NotificationItemRead(
        id=f"email-reply-{reply.id}",
        type="email_reply",
        category="interested_reply",
        status=_reply_status_label(sentiment),
        severity="success",
        channel="email",
        title=title,
        message=getattr(reply, "subject", None) or title,
        content=body,
        preview=_notification_preview(body),
        badge_label="Interested Reply",
        badge_variant="success",
        sender_email=getattr(reply, "from_email", None),
        recipient_email=settings.gmail_sender_email,
        subject=getattr(reply, "subject", None),
        timestamp=received_at,
        timestamp_iso=received_at.isoformat(),
        display_time=_display_time(received_at),
        display_date=_display_date(received_at),
        display_datetime=_display_datetime(received_at),
        lead=lead,
        thread=thread,
        action=_notification_action(category="interested_reply", thread=thread),
        reply_id=thread.reply_id,
        draft_id=thread.draft_id,
        gmail_message_id=thread.gmail_message_id,
        gmail_thread_id=thread.gmail_thread_id,
        event_type="email_reply.interested",
        payload={
            "sentiment": sentiment,
            "stored_sentiment": getattr(reply, "sentiment", None),
            "email_draft_id": getattr(reply, "email_draft_id", None),
            "original_subject": (
                reply.email_draft.subject if getattr(reply, "email_draft", None) else None
            ),
        },
    )


def _workflow_warning_to_notification(log: Any) -> NotificationItemRead:
    timestamp = _safe_datetime(getattr(log, "created_at", None))
    payload = dict(getattr(log, "payload", None) or {})
    thread = _notification_thread(
        reply_id=_payload_int(payload, "reply_id"),
        draft_id=_payload_int(payload, "draft_id"),
        gmail_message_id=payload.get("message_id"),
        gmail_thread_id=payload.get("gmail_thread_id"),
    )
    content = getattr(log, "message", "") or "Workflow warning."
    return NotificationItemRead(
        id=f"system-warning-{log.id}",
        type="system_warning",
        category="system_warning",
        status=getattr(log, "status", "warning"),
        severity="warning",
        channel="system",
        title=_notification_title(
            category="system_warning",
            event_type=getattr(log, "event_type", "") or "",
            lead=_notification_lead(getattr(log, "lead", None)),
            payload=payload,
        ),
        message=content,
        content=content,
        preview=_notification_preview(content),
        badge_label="System Warning",
        badge_variant="warning",
        sender_email=None,
        recipient_email=None,
        subject=None,
        timestamp=timestamp,
        timestamp_iso=timestamp.isoformat(),
        display_time=_display_time(timestamp),
        display_date=_display_date(timestamp),
        display_datetime=_display_datetime(timestamp),
        lead=_notification_lead(getattr(log, "lead", None)),
        thread=thread,
        action=_notification_action(category="system_warning", thread=thread),
        reply_id=thread.reply_id,
        draft_id=thread.draft_id,
        gmail_message_id=thread.gmail_message_id,
        gmail_thread_id=thread.gmail_thread_id,
        event_type=getattr(log, "event_type", None),
        payload=payload,
    )


def _notification_lead(lead: Any) -> NotificationLeadRead | None:
    if lead is None:
        return None
    name = f"{getattr(lead, 'first_name', '')} {getattr(lead, 'last_name', '')}".strip()
    return NotificationLeadRead(
        id=getattr(lead, "id", None),
        name=name or None,
        email=getattr(lead, "email", None),
        company_name=getattr(lead, "company_name", None),
    )


def _notification_summary(items: list[NotificationItemRead]) -> NotificationSummaryRead:
    return NotificationSummaryRead(
        total_alerts=len(items),
        slack_feed_log=sum(1 for item in items if item.type == "slack_notification"),
        hot_alerts=sum(1 for item in items if item.category == "hot_lead"),
        replies_alert=sum(1 for item in items if item.category == "interested_reply"),
        system_warnings=sum(1 for item in items if item.category == "system_warning"),
    )


def _notification_cards(items: list[NotificationItemRead]) -> list[NotificationCardRead]:
    summary = _notification_summary(items)
    return [
        NotificationCardRead(
            key="slack_feed_log",
            label="Slack Feed Log",
            value=summary.slack_feed_log,
            display_value=f"{summary.slack_feed_log} Alerts",
            variant="info",
        ),
        NotificationCardRead(
            key="hot_alerts",
            label="Hot Alerts",
            value=summary.hot_alerts,
            display_value=str(summary.hot_alerts),
            variant="danger",
        ),
        NotificationCardRead(
            key="replies_alert",
            label="Replies Alert",
            value=summary.replies_alert,
            display_value=str(summary.replies_alert),
            variant="success",
        ),
        NotificationCardRead(
            key="system_warnings",
            label="System Warnings",
            value=summary.system_warnings,
            display_value=str(summary.system_warnings),
            variant="warning",
        ),
    ]


def _notification_filters(items: list[NotificationItemRead]) -> list[NotificationFilterRead]:
    return [
        NotificationFilterRead(key="all_alerts", label="All Alerts", count=len(items), active=True),
        NotificationFilterRead(
            key="hot_leads",
            label="HOT Leads",
            count=sum(1 for item in items if item.category == "hot_lead"),
        ),
        NotificationFilterRead(
            key="replies",
            label="Replies",
            count=sum(1 for item in items if item.category == "interested_reply"),
        ),
        NotificationFilterRead(
            key="system_warnings",
            label="System Warnings",
            count=sum(1 for item in items if item.category == "system_warning"),
        ),
    ]


def _notification_thread(
    *,
    reply_id: int | None,
    draft_id: int | None,
    gmail_message_id: str | None,
    gmail_thread_id: str | None,
) -> NotificationThreadRead:
    return NotificationThreadRead(
        reply_id=reply_id,
        draft_id=draft_id,
        gmail_message_id=gmail_message_id,
        gmail_thread_id=gmail_thread_id,
    )


def _notification_action(
    *,
    category: str,
    thread: NotificationThreadRead,
) -> NotificationActionRead | None:
    if category == "interested_reply" and thread.reply_id:
        return NotificationActionRead(label="Open Reply", target=f"/replies/{thread.reply_id}")
    if category == "hot_lead" and thread.draft_id:
        return NotificationActionRead(label="Review Draft", target=f"/drafts/{thread.draft_id}")
    if thread.draft_id:
        return NotificationActionRead(label="Open Draft", target=f"/drafts/{thread.draft_id}")
    return None


def _notification_category(*, event_type: str, item_type: str) -> str:
    if "hot_lead" in event_type:
        return "hot_lead"
    if "interested_reply" in event_type or item_type == "email_reply":
        return "interested_reply"
    if "email_sent" in event_type:
        return "email_sent"
    if "failed" in event_type or "warning" in event_type:
        return "system_warning"
    return "slack_event" if item_type == "slack_notification" else item_type


def _notification_title(
    *,
    category: str,
    event_type: str,
    lead: NotificationLeadRead | None,
    payload: dict[str, Any],
) -> str:
    lead_name = lead.name if lead else None
    company = (lead.company_name if lead else None) or payload.get("company_name")
    if category == "hot_lead":
        score = payload.get("score")
        score_text = f" (Score: {score}/10)" if score is not None else ""
        target = " at ".join(part for part in [lead_name, company] if part)
        return f"New HOT Lead detected: {target}{score_text}" if target else "New HOT Lead detected"
    if category == "interested_reply":
        target = " ".join(part for part in [lead_name, f"({company})" if company else None] if part)
        subject = payload.get("subject")
        suffix = f": {subject}" if subject else ""
        return f"{target or 'Lead'} replied with classification: Interested{suffix}"
    if category == "email_sent":
        target = " at ".join(part for part in [lead_name, company] if part)
        return f"Email sent to {target}" if target else "Email sent"
    if category == "system_warning":
        return f"System warning: {_humanize_event_type(event_type)}"
    return _slack_notification_title(event_type)


def _notification_severity(*, category: str, status: str | None) -> str:
    if status in {"failed", "warning"} or category == "system_warning":
        return "warning"
    if category == "hot_lead":
        return "danger"
    if category == "interested_reply":
        return "success"
    return "info"


def _notification_badge_label(category: str) -> str:
    labels = {
        "hot_lead": "HOT Lead",
        "interested_reply": "Interested Reply",
        "email_sent": "Email Sent",
        "system_warning": "System Warning",
        "slack_event": "Slack",
    }
    return labels.get(category, "Alert")


def _notification_badge_variant(category: str) -> str:
    variants = {
        "hot_lead": "danger",
        "interested_reply": "success",
        "email_sent": "info",
        "system_warning": "warning",
        "slack_event": "info",
    }
    return variants.get(category, "neutral")


def _notification_preview(value: str, *, max_length: int = 140) -> str:
    preview = " ".join((value or "").strip().split())
    if len(preview) <= max_length:
        return preview
    return f"{preview[: max_length - 3].rstrip()}..."


def _payload_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _slack_notification_title(event_type: str) -> str:
    titles = {
        "slack.email_sent_notification_sent": "Slack email sent notification",
        "slack.interested_reply_notification_sent": "Slack interested reply notification",
        "slack.hot_lead_notification_sent": "Slack hot lead notification",
        "slack.test_notification_sent": "Slack test notification",
    }
    return titles.get(event_type, "Slack notification")


def _humanize_event_type(event_type: str) -> str:
    cleaned = event_type.replace(".", " ").replace("_", " ").strip()
    return cleaned.capitalize() if cleaned else "workflow issue"


# ──────────────────────────────────────────────────────────────
# Workflow Logs
# ──────────────────────────────────────────────────────────────


@app.get("/logs", response_model=list[WorkflowLogRead], dependencies=AUTH_REQUIRED)
def list_workflow_logs(
    lead_id: int | None = Query(default=None),
    event_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[WorkflowLogRead]:
    query = db.query(WorkflowLogRepository.model)
    if lead_id:
        query = query.filter(WorkflowLogRepository.model.lead_id == lead_id)
    if event_type:
        query = query.filter(WorkflowLogRepository.model.event_type == event_type)
    return query.order_by(WorkflowLogRepository.model.created_at.desc()).limit(limit).all()


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)
