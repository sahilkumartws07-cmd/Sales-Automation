from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sales_automation.models.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from sales_automation.models.email_draft import EmailDraft
    from sales_automation.models.email_reply import EmailReply
    from sales_automation.models.lead import Lead


class EmailSentMessage(Base, IdMixin, TimestampMixin):
    __tablename__ = "email_sent_messages"

    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    email_draft_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_drafts.id", ondelete="CASCADE"), index=True
    )
    email_reply_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_replies.id", ondelete="SET NULL"), index=True
    )
    to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    gmail_message_id: Mapped[str | None] = mapped_column(String(255))
    gmail_thread_id: Mapped[str | None] = mapped_column(String(255), index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    lead: Mapped["Lead"] = relationship(back_populates="sent_messages")
    email_draft: Mapped["EmailDraft | None"] = relationship(back_populates="sent_messages")
    email_reply: Mapped["EmailReply | None"] = relationship(back_populates="sent_messages")
