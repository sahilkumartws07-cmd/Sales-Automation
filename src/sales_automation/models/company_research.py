from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sales_automation.models.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from sales_automation.models.lead import Lead


class CompanyResearch(Base, IdMixin, TimestampMixin):
    __tablename__ = "company_research"

    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    company_domain: Mapped[str | None] = mapped_column(String(255))
    website_url: Mapped[str | None] = mapped_column(String(500))
    extracted_content: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    pain_points: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    signals: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    model: Mapped[str | None] = mapped_column(String(120))

    lead: Mapped["Lead"] = relationship(back_populates="research")
