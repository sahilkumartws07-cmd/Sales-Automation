from __future__ import annotations

from sqlalchemy.orm import Session

from sales_automation.models import CompanyResearch, EmailDraft, LeadScore
from sales_automation.repositories import (
    CompanyResearchRepository,
    EmailDraftRepository,
    LeadRepository,
    LeadScoreRepository,
    WorkflowLogRepository,
)
from sales_automation.services.lead_payload import lead_to_payload
from sales_automation.services.openai_service import OpenAIService


class SalesWorkflowService:
    def __init__(self, session: Session, openai_service: OpenAIService | None = None) -> None:
        self.session = session
        self.openai_service = openai_service or OpenAIService()
        self.leads = LeadRepository(session)
        self.research = CompanyResearchRepository(session)
        self.scores = LeadScoreRepository(session)
        self.drafts = EmailDraftRepository(session)
        self.logs = WorkflowLogRepository(session)

    def enrich_score_and_draft(self, lead_id: int) -> EmailDraft:
        lead = self.leads.get(lead_id)
        if lead is None:
            raise ValueError(f"Lead not found: {lead_id}")

        research_payload = self.openai_service.research_company(
            company_name=lead.company_name,
            lead_title=lead.title,
        )
        research = self.research.add(
            CompanyResearch(
                lead_id=lead.id,
                company_domain=research_payload.get("company_domain"),
                summary=research_payload["summary"],
                pain_points=research_payload.get("pain_points", []),
                signals=research_payload.get("signals", []),
                sources=research_payload.get("sources", []),
                model=self.openai_service.settings.nvidia_model,
            )
        )

        lead_payload = lead_to_payload(lead)
        score_payload = self.openai_service.score_lead(
            lead=lead_payload,
            research={
                "summary": research.summary,
                "pain_points": research.pain_points,
                "signals": research.signals,
            },
        )
        score = self.scores.add(
            LeadScore(
                lead_id=lead.id,
                score=int(score_payload["score"]),
                grade=str(score_payload["grade"]),
                rationale=str(score_payload["rationale"]),
                factors=score_payload.get("factors", {}),
                model=self.openai_service.settings.nvidia_model,
            )
        )

        draft_payload = self.openai_service.draft_email(
            lead=lead_payload,
            research={"summary": research.summary, "signals": research.signals},
            score={"score": score.score, "grade": score.grade, "rationale": score.rationale},
        )
        draft = self.drafts.add(
            EmailDraft(
                lead_id=lead.id,
                subject=draft_payload["subject"],
                body=draft_payload["body"],
                status="pending_approval",
                model=self.openai_service.settings.nvidia_model,
            )
        )
        self.logs.record(
            lead_id=lead.id,
            event_type="lead.enriched_scored_drafted",
            status="completed",
            message="Lead research, score, and email draft created.",
        )
        return draft
