from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sales_automation.models.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from sales_automation.models.company_research import CompanyResearch
    from sales_automation.models.email_draft import EmailDraft
    from sales_automation.models.email_reply import EmailReply
    from sales_automation.models.email_sent_message import EmailSentMessage
    from sales_automation.models.lead_score import LeadScore
    from sales_automation.models.workflow_log import WorkflowLog


class Lead(Base, IdMixin, TimestampMixin):
    __tablename__ = "leads"

    first_name: Mapped[str] = mapped_column(String(120), nullable=False)
    last_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(180))
    company_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), index=True, default="new", nullable=False)
    lead_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )

    research: Mapped[list["CompanyResearch"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    scores: Mapped[list["LeadScore"]] = relationship(back_populates="lead", cascade="all, delete-orphan")
    email_drafts: Mapped[list["EmailDraft"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    email_replies: Mapped[list["EmailReply"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    sent_messages: Mapped[list["EmailSentMessage"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    workflow_logs: Mapped[list["WorkflowLog"]] = relationship(back_populates="lead")
