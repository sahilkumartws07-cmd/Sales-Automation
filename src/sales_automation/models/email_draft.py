from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sales_automation.models.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from sales_automation.models.email_approval import EmailApproval
    from sales_automation.models.email_reply import EmailReply
    from sales_automation.models.email_sent_message import EmailSentMessage
    from sales_automation.models.lead import Lead


class EmailDraft(Base, IdMixin, TimestampMixin):
    __tablename__ = "email_drafts"

    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), index=True, default="draft", nullable=False)
    model: Mapped[str | None] = mapped_column(String(120))

    lead: Mapped["Lead"] = relationship(back_populates="email_drafts")
    approvals: Mapped[list["EmailApproval"]] = relationship(
        back_populates="email_draft", cascade="all, delete-orphan"
    )
    replies: Mapped[list["EmailReply"]] = relationship(back_populates="email_draft")
    sent_messages: Mapped[list["EmailSentMessage"]] = relationship(back_populates="email_draft")
