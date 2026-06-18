from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sales_automation.models.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from sales_automation.models.email_draft import EmailDraft


class EmailApproval(Base, IdMixin, TimestampMixin):
    __tablename__ = "email_approvals"

    email_draft_id: Mapped[int] = mapped_column(
        ForeignKey("email_drafts.id", ondelete="CASCADE"), index=True
    )
    approved_by: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    email_draft: Mapped["EmailDraft"] = relationship(back_populates="approvals")
