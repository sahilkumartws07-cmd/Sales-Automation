from __future__ import annotations

from dataclasses import dataclass
import logging
from time import monotonic
from typing import Any

from sqlalchemy.orm import Session

from sales_automation.models import LeadScore
from sales_automation.repositories import (
    CompanyResearchRepository,
    LeadRepository,
    LeadScoreRepository,
    WorkflowLogRepository,
)
from sales_automation.services.lead_payload import lead_to_payload
from sales_automation.services.openai_service import OpenAIService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LeadScoringResult:
    scored: int
    skipped: int
    failed: int
    errors: list[dict[str, Any]]


class AILeadScoringService:
    def __init__(self, session: Session, openai_service: OpenAIService | None = None) -> None:
        self.session = session
        self.openai_service = openai_service or OpenAIService()
        self.leads = LeadRepository(session)
        self.research = CompanyResearchRepository(session)
        self.scores = LeadScoreRepository(session)
        self.logs = WorkflowLogRepository(session)

    def score_unscored_leads(
        self,
        *,
        limit: int = 100,
        max_seconds: float | None = None,
    ) -> LeadScoringResult:
        scored = 0
        skipped = 0
        failed = 0
        errors: list[dict[str, Any]] = []
        started_at = monotonic()
        leads = self.scores.list_researched_unscored_leads(limit=limit)

        self.logs.record(
            event_type="lead_scoring.batch_started",
            status="started",
            message="Started AI lead scoring batch.",
            payload={"limit": limit, "candidate_count": len(leads)},
        )
        self.session.commit()

        for lead in leads:
            if max_seconds is not None and monotonic() - started_at >= max_seconds:
                break

            lead_id = lead.id
            company_name = lead.company_name
            try:
                if self.scores.has_score_for_lead(lead_id):
                    skipped += 1
                    continue
                self.score_lead(lead_id)
                lead.status = "scored"
                self.session.commit()
                scored += 1
            except Exception as exc:
                self.session.rollback()
                failed += 1
                errors.append({"lead_id": lead_id, "error": str(exc)})
                self.logs.record(
                    lead_id=lead_id,
                    event_type="lead_scoring.failed",
                    status="failed",
                    message=str(exc),
                    payload={"lead_id": lead_id, "company_name": company_name},
                )
                self.session.commit()
                logger.exception("lead_scoring_failed", extra={"lead_id": lead_id})

        self.logs.record(
            event_type="lead_scoring.batch_completed",
            status="completed",
            message="AI lead scoring batch completed.",
            payload={"scored": scored, "skipped": skipped, "failed": failed},
        )
        self.session.commit()
        return LeadScoringResult(scored=scored, skipped=skipped, failed=failed, errors=errors)

    def score_lead(self, lead_id: int) -> LeadScore:
        lead = self.leads.get(lead_id)
        if lead is None:
            raise ValueError(f"Lead not found: {lead_id}")
        if self.scores.has_score_for_lead(lead_id):
            raise ValueError(f"Lead already scored: {lead_id}")

        research = self.research.latest_for_lead(lead_id)
        if research is None:
            raise ValueError(f"No company research found for lead: {lead_id}")

        self.logs.record(
            lead_id=lead.id,
            event_type="lead_scoring.started",
            status="started",
            message="Started AI lead scoring.",
            payload={"lead_id": lead.id, "company_name": lead.company_name},
        )

        signals = _normalize_items(research.signals)
        pain_points = _normalize_items(research.pain_points)
        try:
            response = self.openai_service.score_lead_from_research(
                lead=lead_to_payload(lead),
                company_summary=_truncate(research.summary, 1_500),
                signals=signals[:5],
                pain_points=pain_points[:5],
            )
            score = _normalize_score_response(response)
        except Exception as exc:
            score = _fallback_score_response(
                lead=lead_to_payload(lead),
                company_summary=research.summary,
                signals=signals,
                pain_points=pain_points,
                error=str(exc),
            )
            self.logs.record(
                lead_id=lead.id,
                event_type="lead_scoring.ai_fallback",
                status="completed",
                message="AI lead scoring failed; local fallback score was used.",
                payload={"error": str(exc), "score": score["score"], "category": score["category"]},
            )
        stored = self.scores.add(
            LeadScore(
                lead_id=lead.id,
                score=score["score"],
                grade=score["category"],
                rationale=score["reason"],
                factors=score["factors"],
                model=self.openai_service.settings.nvidia_model,
            )
        )
        self.logs.record(
            lead_id=lead.id,
            event_type="lead_scoring.completed",
            status="completed",
            message="AI lead scoring completed.",
            payload={"score": stored.score, "category": stored.grade},
        )
        return stored


def _normalize_score_response(response: dict[str, Any]) -> dict[str, Any]:
    score = int(response["score"])
    if score < 1 or score > 10:
        raise ValueError(f"OpenAI returned score outside 1-10: {score}")

    category = str(response["category"]).upper()
    if category not in {"HOT", "WARM", "COLD"}:
        raise ValueError(f"OpenAI returned invalid category: {category}")

    return {
        "score": score,
        "category": category,
        "reason": str(response["reason"]),
        "factors": dict(response.get("factors") or {}),
    }


def _normalize_items(items: list[Any] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items or []:
        if isinstance(item, dict):
            normalized.append(item)
        else:
            normalized.append({"text": str(item)})
    return normalized


def _fallback_score_response(
    *,
    lead: dict[str, Any],
    company_summary: str,
    signals: list[dict[str, Any]],
    pain_points: list[dict[str, Any]],
    error: str,
) -> dict[str, Any]:
    evidence_count = len(signals) + len(pain_points)
    has_title = bool(lead.get("title"))
    has_summary = bool(company_summary.strip())

    score = 4
    if has_summary:
        score += 2
    if evidence_count >= 2:
        score += 2
    elif evidence_count == 1:
        score += 1
    if has_title:
        score += 1

    score = max(1, min(score, 10))
    if score >= 8:
        category = "HOT"
    elif score >= 5:
        category = "WARM"
    else:
        category = "COLD"

    return {
        "score": score,
        "category": category,
        "reason": (
            "Fallback score generated from available company research because AI scoring "
            f"did not complete: {error}"
        ),
        "factors": {
            "fit": "Estimated from company summary and lead title.",
            "urgency": "Estimated from available buying signals and pain points.",
            "evidence": f"{evidence_count} research signals or pain points available.",
        },
    }


def _truncate(value: str, max_chars: int) -> str:
    return value[:max_chars]
