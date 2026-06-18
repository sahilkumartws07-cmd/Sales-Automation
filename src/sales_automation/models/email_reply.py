from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sales_automation.models.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from sales_automation.models.email_draft import EmailDraft
    from sales_automation.models.email_sent_message import EmailSentMessage
    from sales_automation.models.lead import Lead


class EmailReply(Base, IdMixin, TimestampMixin):
    __tablename__ = "email_replies"

    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    email_draft_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_drafts.id", ondelete="SET NULL")
    )
    from_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sentiment: Mapped[str | None] = mapped_column(String(40))
    gmail_message_id: Mapped[str | None] = mapped_column(String(255))
    gmail_thread_id: Mapped[str | None] = mapped_column(String(255), index=True)
    gmail_rfc_message_id: Mapped[str | None] = mapped_column(String(255))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    lead: Mapped["Lead"] = relationship(back_populates="email_replies")
    email_draft: Mapped["EmailDraft | None"] = relationship(back_populates="replies")
    sent_messages: Mapped[list["EmailSentMessage"]] = relationship(back_populates="email_reply")
