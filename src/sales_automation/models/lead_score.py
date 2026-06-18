from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sales_automation.models.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from sales_automation.models.lead import Lead


class LeadScore(Base, IdMixin, TimestampMixin):
    __tablename__ = "lead_scores"
    __table_args__ = (CheckConstraint("score >= 0 AND score <= 100", name="ck_lead_scores_score_range"),)

    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    score: Mapped[int] = mapped_column(nullable=False)
    grade: Mapped[str] = mapped_column(String(8), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    factors: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    model: Mapped[str | None] = mapped_column(String(120))

    lead: Mapped["Lead"] = relationship(back_populates="scores")
