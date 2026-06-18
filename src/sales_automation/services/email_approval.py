from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging

from sqlalchemy.orm import Session

from sales_automation.models import EmailApproval, EmailDraft
from sales_automation.repositories import (
    EmailApprovalRepository,
    EmailDraftRepository,
    WorkflowLogRepository,
)
from sales_automation.services.gmail_service import GmailService
from sales_automation.services.slack_notification import SlackNotificationService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApprovalLink:
    draft_id: int
    approve_url: str
    reject_url: str


class EmailApprovalService:
    def __init__(
        self,
        session: Session,
        *,
        slack_service: SlackNotificationService | None = None,
    ) -> None:
        self.session = session
        self.drafts = EmailDraftRepository(session)
        self.approvals = EmailApprovalRepository(session)
        self.logs = WorkflowLogRepository(session)
        self.slack_service = slack_service

    def list_pending(self, *, limit: int = 100) -> list[EmailDraft]:
        return self.drafts.list_pending_approval(limit=limit)

    def approval_link_for_draft(self, draft_id: int, *, base_url: str) -> ApprovalLink:
        base = base_url.rstrip("/")
        return ApprovalLink(
            draft_id=draft_id,
            approve_url=f"{base}/drafts/{draft_id}/approve",
            reject_url=f"{base}/drafts/{draft_id}/reject",
        )

    def approve(self, draft_id: int, *, approved_by: str, notes: str | None = None) -> EmailDraft:
        return self._record_decision(
            draft_id,
            approved_by=approved_by,
            status="approved",
            draft_status="approved",
            notes=notes,
        )

    def reject(self, draft_id: int, *, approved_by: str, notes: str | None = None) -> EmailDraft:
        return self._record_decision(
            draft_id,
            approved_by=approved_by,
            status="rejected",
            draft_status="rejected",
            notes=notes,
        )

    def edit(
        self,
        draft_id: int,
        *,
        subject: str | None = None,
        body: str | None = None,
    ) -> EmailDraft:
        draft = self._get_draft(draft_id)
        if subject is not None:
            draft.subject = subject
        if body is not None:
            draft.body = body
        draft.status = "pending_approval"
        self.logs.record(
            lead_id=draft.lead_id,
            event_type="email_approval.edited",
            status="completed",
            message="Email draft edited and returned to pending approval.",
            payload={"draft_id": draft.id},
        )
        return draft

    def mark_sent(self, draft_id: int, *, message_id: str | None = None) -> EmailDraft:
        draft = self._get_draft(draft_id)
        draft.status = "sent"
        self.logs.record(
            lead_id=draft.lead_id,
            event_type="email.sent",
            status="completed",
            message="Approved email sent.",
            payload={"draft_id": draft.id, "message_id": message_id},
        )
        return draft

    def send_approved(
        self,
        draft_id: int,
        *,
        gmail_service: GmailService | None = None,
        notify_slack: bool = True,
    ) -> EmailDraft:
        draft = self._get_draft(draft_id)
        if draft.status != "approved":
            raise ValueError(f"Draft must be approved before sending: {draft_id}")
        gmail = gmail_service or GmailService()
        message_id = gmail.send_draft(lead=draft.lead, draft=draft)
        sent_draft = self.mark_sent(draft.id, message_id=message_id)
        if notify_slack:
            self._notify_email_sent(draft=sent_draft, message_id=message_id)
        return sent_draft

    def _record_decision(
        self,
        draft_id: int,
        *,
        approved_by: str,
        status: str,
        draft_status: str,
        notes: str | None,
    ) -> EmailDraft:
        draft = self._get_draft(draft_id)
        approval = EmailApproval(
            email_draft_id=draft.id,
            approved_by=approved_by,
            status=status,
            notes=notes,
            approved_at=datetime.now(UTC),
        )
        self.approvals.add(approval)
        draft.status = draft_status
        self.logs.record(
            lead_id=draft.lead_id,
            event_type=f"email_approval.{status}",
            status="completed",
            message=f"Email draft {status}.",
            payload={"draft_id": draft.id, "approved_by": approved_by},
        )
        return draft

    def _get_draft(self, draft_id: int) -> EmailDraft:
        draft = self.drafts.get(draft_id)
        if draft is None:
            raise ValueError(f"Email draft not found: {draft_id}")
        return draft

    def _notify_email_sent(self, *, draft: EmailDraft, message_id: str | None = None) -> None:
        try:
            slack = self.slack_service or SlackNotificationService()
            slack.notify_email_sent(lead=draft.lead, draft=draft, message_id=message_id)
        except Exception as exc:
            self.logs.record(
                lead_id=draft.lead_id,
                event_type="slack.email_sent_notification_failed",
                status="failed",
                message=str(exc),
                payload={"draft_id": draft.id, "message_id": message_id},
            )
            logger.exception(
                "slack_email_sent_notification_failed",
                extra={"draft_id": draft.id, "lead_id": draft.lead_id},
            )
            return

        self.logs.record(
            lead_id=draft.lead_id,
            event_type="slack.email_sent_notification_sent",
            status="completed",
            message="Slack email sent notification delivered.",
            payload={"draft_id": draft.id, "message_id": message_id},
        )
