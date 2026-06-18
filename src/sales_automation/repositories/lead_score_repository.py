from __future__ import annotations

from sqlalchemy import exists, select

from sales_automation.models import CompanyResearch, Lead, LeadScore
from sales_automation.repositories.base import BaseRepository


class LeadScoreRepository(BaseRepository[LeadScore]):
    model = LeadScore

    def has_score_for_lead(self, lead_id: int) -> bool:
        return self.exists_for_lead(lead_id)

    def list_researched_unscored_leads(self, *, limit: int = 100) -> list[Lead]:
        statement = (
            select(Lead)
            .where(exists().where(CompanyResearch.lead_id == Lead.id))
            .where(~exists().where(LeadScore.lead_id == Lead.id))
            .order_by(Lead.created_at.asc())
            .limit(limit)
        )
        return list(self.session.scalars(statement))
