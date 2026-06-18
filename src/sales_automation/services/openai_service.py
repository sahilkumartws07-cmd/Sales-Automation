from __future__ import annotations

import logging

from sales_automation.services.nvidia_ai_utils import NvidiaAIUtility

logger = logging.getLogger(__name__)


class OpenAIService:
    def __init__(self, settings=None, client=None) -> None:
        self._utility = NvidiaAIUtility(settings=settings, client=client)
        self.settings = self._utility.settings

    def research_company(self, *, company_name: str, lead_title: str | None = None) -> dict:
        return self._utility.research_company(company_name=company_name, lead_title=lead_title)

    def summarize_website(self, *, company_name: str, website_url: str, content: str) -> dict:
        return self._utility.summarize_website(
            company_name=company_name, website_url=website_url, content=content
        )

    def score_lead(self, *, lead: dict, research: dict | None = None) -> dict:
        return self._utility.score_lead(lead=lead, research=research)

    def score_lead_from_research(
        self,
        *,
        lead: dict,
        company_summary: str,
        signals: list | None = None,
        pain_points: list | None = None,
    ) -> dict:
        return self._utility.score_lead_from_research(
            lead=lead,
            company_summary=company_summary,
            signals=signals,
            pain_points=pain_points,
        )

    def draft_email(self, *, lead: dict, research: dict | None = None, score: dict | None = None) -> dict:
        return self._utility.draft_email(lead=lead, research=research, score=score)

    def generate_cold_email(self, *, lead: dict, company_summary: str, lead_category: str) -> dict:
        return self._utility.generate_cold_email(
            lead=lead, company_summary=company_summary, lead_category=lead_category
        )

    def classify_email_reply(self, *, from_email: str, subject: str | None, body: str) -> dict:
        return self._utility.classify_email_reply(
            from_email=from_email, subject=subject, body=body
        )
