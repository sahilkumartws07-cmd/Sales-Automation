from __future__ import annotations

from sqlalchemy import exists, select

from sales_automation.models import CompanyResearch, EmailDraft, Lead, LeadScore
from sales_automation.repositories.base import BaseRepository


class LeadRepository(BaseRepository[Lead]):
    model = Lead

    def get_by_email(self, email: str) -> Lead | None:
        return self.session.scalar(select(Lead).where(Lead.email == email))

    def list_by_status(self, status: str, *, limit: int = 100) -> list[Lead]:
        statement = (
            select(Lead)
            .where(Lead.status == status)
            .order_by(Lead.created_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def list_qualified_for_email(
        self, categories: list[str], *, limit: int = 100
    ) -> list[Lead]:
        statement = (
            select(Lead)
            .join(LeadScore)
            .where(LeadScore.grade.in_(categories))
            .where(exists(select(CompanyResearch.id).where(CompanyResearch.lead_id == Lead.id)))
            .where(~exists(select(EmailDraft.id).where(EmailDraft.lead_id == Lead.id)))
            .order_by(Lead.created_at.asc())
            .limit(limit)
        )
        return list(self.session.scalars(statement))
