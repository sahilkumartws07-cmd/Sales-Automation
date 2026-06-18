from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from sales_automation.config import Settings, get_settings
from sales_automation.models import EmailDraft, EmailReply, Lead
from sales_automation.repositories import (
    CompanyResearchRepository,
    EmailDraftRepository,
    EmailReplyRepository,
    LeadRepository,
    LeadScoreRepository,
    WorkflowLogRepository,
)
from sales_automation.services.gmail_service import clean_reply_body
from sales_automation.services.reply_classification import classify_obvious_reply_intent


class GoogleSheetsLoggingService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        client: Any | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.client = client
        self.leads = LeadRepository(session)
        self.research = CompanyResearchRepository(session)
        self.scores = LeadScoreRepository(session)
        self.drafts = EmailDraftRepository(session)
        self.replies = EmailReplyRepository(session)
        self.logs = WorkflowLogRepository(session)

    def append_lead_row(self, lead_id: int) -> list[Any]:
        lead = self.leads.get(lead_id)
        if lead is None:
            raise ValueError(f"Lead not found: {lead_id}")

        research = self.research.latest_for_lead(lead.id)
        score = self.scores.latest_for_lead(lead.id)
        draft = self.drafts.latest_for_lead(lead.id)
        replies = self.replies.list_for_lead(lead.id)
        reply = replies[0] if replies else None

        row = workflow_log_row(
            lead=lead,
            company_summary=research.summary if research else None,
            score=score.score if score else None,
            score_reason=score.rationale if score else None,
            email_draft=draft,
            approval_status=draft.status if draft else None,
            reply=reply,
        )
        self._worksheet().append_row(row)
        self.logs.record(
            lead_id=lead.id,
            event_type="google_sheets.row_appended",
            status="completed",
            message="Workflow row appended to Google Sheets.",
            payload={"spreadsheet_id": self.settings.google_sheets_spreadsheet_id},
        )
        return row

    def _worksheet(self) -> Any:
        if not self.settings.google_sheets_spreadsheet_id:
            raise ValueError("GOOGLE_SHEETS_SPREADSHEET_ID is not configured")
        client = self.client or _gspread_client(self.settings)
        spreadsheet = client.open_by_key(self.settings.google_sheets_spreadsheet_id)
        return spreadsheet.worksheet(self.settings.google_sheets_worksheet_name)


def workflow_log_row(
    *,
    lead: Lead,
    company_summary: str | None,
    score: int | None,
    score_reason: str | None,
    email_draft: EmailDraft | None,
    approval_status: str | None,
    reply: EmailReply | None,
) -> list[Any]:
    return [
        datetime.now(UTC).isoformat(),
        lead.company_name,
        lead.email,
        lead.title or "",
        score if score is not None else "",
        score_reason or "",
        company_summary or "",
        email_draft.subject if email_draft else "",
        email_draft.body if email_draft else "",
        approval_status or "",
        _effective_reply_sentiment(reply) if reply else "",
    ]


def _gspread_client(settings: Settings) -> Any:
    if not settings.google_sheets_credentials_file:
        raise ValueError("GOOGLE_SHEETS_CREDENTIALS_FILE is not configured")
    try:
        import gspread
    except ImportError as exc:
        raise RuntimeError("Install gspread to enable Google Sheets logging") from exc
    return gspread.service_account(filename=settings.google_sheets_credentials_file)


def _effective_reply_sentiment(reply: EmailReply) -> str:
    obvious = classify_obvious_reply_intent(clean_reply_body(reply.body or ""))
    if obvious and obvious["classification"] in {"INTERESTED", "NOT_INTERESTED"}:
        return str(obvious["classification"])
    return _canonical_reply_sentiment(reply.sentiment) or ""


def _canonical_reply_sentiment(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = "".join(char if char.isalnum() else "_" for char in value.strip()).strip("_").upper()
    aliases = {
        "INTERESTED": "INTERESTED",
        "NOT_INTERESTED": "NOT_INTERESTED",
        "NOTINTERESTED": "NOT_INTERESTED",
        "NO_INTEREST": "NOT_INTERESTED",
        "OUT_OF_OFFICE": "OUT_OF_OFFICE",
        "OOO": "OUT_OF_OFFICE",
        "NEEDS_FOLLOW_UP": "NEEDS_FOLLOW_UP",
        "FOLLOW_UP": "NEEDS_FOLLOW_UP",
    }
    return aliases.get(cleaned, cleaned or None)
