from __future__ import annotations

from sales_automation.models import CompanyResearch
from sales_automation.repositories.base import BaseRepository


class CompanyResearchRepository(BaseRepository[CompanyResearch]):
    model = CompanyResearch
