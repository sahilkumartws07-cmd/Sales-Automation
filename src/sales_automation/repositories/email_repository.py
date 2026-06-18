from __future__ import annotations

from sqlalchemy import select

from sales_automation.models import EmailApproval, EmailDraft, EmailReply, EmailSentMessage
from sales_automation.repositories.base import BaseRepository


class EmailDraftRepository(BaseRepository[EmailDraft]):
    model = EmailDraft

    def list_pending_approval(self, *, limit: int = 100) -> list[EmailDraft]:
        statement = (
            select(EmailDraft)
            .where(EmailDraft.status == "pending_approval")
            .order_by(EmailDraft.created_at.asc())
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def get_by_lead_id(self, lead_id: int) -> EmailDraft | None:
        """Get the most recent draft for a lead."""
        return self.latest_for_lead(lead_id)

    def list_sent(self, *, limit: int = 100) -> list[EmailDraft]:
        statement = (
            select(EmailDraft)
            .where(EmailDraft.status == "sent")
            .order_by(EmailDraft.updated_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(statement))


class EmailApprovalRepository(BaseRepository[EmailApproval]):
    model = EmailApproval


class EmailReplyRepository(BaseRepository[EmailReply]):
    model = EmailReply

    def list_for_lead(self, lead_id: int) -> list[EmailReply]:
        statement = (
            select(EmailReply)
            .where(EmailReply.lead_id == lead_id)
            .order_by(EmailReply.received_at.desc())
        )
        return list(self.session.scalars(statement))

    def list_for_draft(self, draft_id: int) -> list[EmailReply]:
        statement = (
            select(EmailReply)
            .where(EmailReply.email_draft_id == draft_id)
            .order_by(EmailReply.received_at.asc())
        )
        return list(self.session.scalars(statement))

    def list_unclassified(self, *, limit: int = 100) -> list[EmailReply]:
        statement = (
            select(EmailReply)
            .where(EmailReply.sentiment.is_(None))
            .order_by(EmailReply.received_at.asc())
            .limit(limit)
        )
        return list(self.session.scalars(statement))


class EmailSentMessageRepository(BaseRepository[EmailSentMessage]):
    model = EmailSentMessage

    def list_for_draft(self, draft_id: int) -> list[EmailSentMessage]:
        statement = (
            select(EmailSentMessage)
            .where(EmailSentMessage.email_draft_id == draft_id)
            .order_by(EmailSentMessage.sent_at.asc(), EmailSentMessage.id.asc())
        )
        return list(self.session.scalars(statement))
