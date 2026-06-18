from __future__ import annotations

import logging
from dataclasses import dataclass
from time import monotonic
from typing import Any

from sqlalchemy.orm import Session

from sales_automation.models import EmailDraft, Lead
from sales_automation.repositories import (
    CompanyResearchRepository,
    EmailDraftRepository,
    LeadRepository,
    LeadScoreRepository,
    WorkflowLogRepository,
)
from sales_automation.config import Settings, get_settings
from sales_automation.services.email_approval import EmailApprovalService
from sales_automation.services.lead_payload import lead_to_payload
from sales_automation.services.openai_service import OpenAIService
from sales_automation.services.slack_notification import SlackNotificationService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailGenerationResult:
    generated: int
    skipped: int
    failed: int
    errors: list[dict[str, Any]]


class ColdEmailGenerationService:
    """Generate professional cold emails for HOT and WARM leads using OpenAI.

    Features:
    - Processes HOT and WARM leads only
    - Uses lead data and company research
    - Generates subject line, body, and CTA
    - Avoids duplicate drafts
    - Stores drafts in PostgreSQL
    - Logs workflow events with timestamps
    - Uses OpenAI structured outputs
    """

    def __init__(
        self,
        session: Session,
        openai_service: OpenAIService | None = None,
        settings: Settings | None = None,
        slack_service: SlackNotificationService | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.openai_service = openai_service or OpenAIService()
        self.slack_service = slack_service
        self.leads = LeadRepository(session)
        self.scores = LeadScoreRepository(session)
        self.research = CompanyResearchRepository(session)
        self.emails = EmailDraftRepository(session)
        self.logs = WorkflowLogRepository(session)
        self.approvals = EmailApprovalService(session)

    def generate_emails_for_qualified_leads(
        self,
        *,
        categories: list[str] | None = None,
        limit: int = 100,
        notify_slack: bool = False,
        max_seconds: float | None = None,
    ) -> EmailGenerationResult:
        """Generate cold emails for HOT and WARM leads with company research.

        Args:
            categories: Lead categories to process (default: ["HOT", "WARM"])
            limit: Maximum number of leads to process

        Returns:
            EmailGenerationResult with counts of generated, skipped, and failed emails
        """
        if categories is None:
            categories = ["HOT", "WARM"]

        generated = 0
        skipped = 0
        failed = 0
        errors: list[dict[str, Any]] = []
        started_at = monotonic()

        # Get qualified leads with scores and research
        leads = self._get_qualified_leads(categories, limit=limit)

        self.logs.record(
            event_type="email_generation.batch_started",
            status="started",
            message=f"Started cold email generation for {categories} leads.",
            payload={
                "categories": categories,
                "candidate_count": len(leads),
                "limit": limit,
            },
        )
        self.session.commit()

        for lead in leads:
            if max_seconds is not None and monotonic() - started_at >= max_seconds:
                break

            lead_id = lead.id
            company_name = lead.company_name
            try:
                score = self.scores.latest_for_lead(lead_id)
                research = self.research.latest_for_lead(lead_id)

                if not score or not research:
                    skipped += 1
                    errors.append(
                        {
                            "lead_id": lead_id,
                            "reason": "Missing score or research",
                        }
                    )
                    continue

                # Check for existing draft
                existing_draft = self.emails.get_by_lead_id(lead_id)
                if existing_draft:
                    skipped += 1
                    continue

                # Generate email
                self._generate_and_store_email(
                    lead=lead,
                    score=score,
                    research=research,
                    notify_slack=notify_slack,
                )
                generated += 1
                self.session.commit()

            except Exception as exc:
                self.session.rollback()
                failed += 1
                errors.append(
                    {
                        "lead_id": lead_id,
                        "error": str(exc),
                    }
                )
                self.logs.record(
                    lead_id=lead_id,
                    event_type="email_generation.failed",
                    status="failed",
                    message=str(exc),
                    payload={"lead_id": lead_id, "company_name": company_name},
                )
                self.session.commit()
                logger.exception("email_generation_failed", extra={"lead_id": lead_id})

        status = "completed" if not errors else "completed_with_errors"
        self.logs.record(
            event_type="email_generation.batch_completed",
            status=status,
            message="Cold email generation batch completed.",
            payload={
                "generated": generated,
                "skipped": skipped,
                "failed": failed,
                "error_count": len(errors),
            },
        )
        self.session.commit()

        return EmailGenerationResult(
            generated=generated,
            skipped=skipped,
            failed=failed,
            errors=errors,
        )

    def generate_email_for_lead(self, lead_id: int) -> EmailDraft:
        """Generate a cold email for a specific lead.

        Args:
            lead_id: ID of the lead

        Returns:
            EmailDraft object

        Raises:
            ValueError: If lead, score, or research not found; or draft already exists
        """
        lead = self.leads.get(lead_id)
        if not lead:
            raise ValueError(f"Lead not found: {lead_id}")

        score = self.scores.latest_for_lead(lead_id)
        if not score:
            raise ValueError(f"No score found for lead: {lead_id}")

        research = self.research.latest_for_lead(lead_id)
        if not research:
            raise ValueError(f"No research found for lead: {lead_id}")

        existing = self.emails.get_by_lead_id(lead_id)
        if existing:
            raise ValueError(f"Draft already exists for lead: {lead_id}")

        return self._generate_and_store_email(
            lead=lead,
            score=score,
            research=research,
        )

    def _get_qualified_leads(self, categories: list[str], *, limit: int = 100) -> list[Lead]:
        """Get scored leads with specified categories that don't have drafts."""
        return self.leads.list_qualified_for_email(categories, limit=limit)

    def _generate_and_store_email(
        self,
        *,
        lead: Lead,
        score: Any,
        research: Any,
        notify_slack: bool = False,
    ) -> EmailDraft:
        """Generate email using OpenAI and store as draft."""
        self.logs.record(
            lead_id=lead.id,
            event_type="email_generation.started",
            status="started",
            message="Started cold email generation.",
            payload={"lead_id": lead.id, "company_name": lead.company_name},
        )

        # Prepare lead data for OpenAI
        lead_payload = lead_to_payload(
            lead,
            title_default="Unknown Title",
            include_source=False,
            include_metadata=False,
        )

        # Generate email using OpenAI with structured outputs
        email_response = self.openai_service.generate_cold_email(
            lead=lead_payload,
            company_summary=research.summary,
            lead_category=score.grade,
        )

        # Create and store email draft
        draft = EmailDraft(
            lead_id=lead.id,
            subject=email_response["subject"],
            body=f"{email_response['body']}\n\n{email_response['call_to_action']}",
            status="pending_approval",
            model=self.openai_service.settings.nvidia_model,
        )
        stored = self.emails.add(draft)

        self.logs.record(
            lead_id=lead.id,
            event_type="email_generation.completed",
            status="completed",
            message="Cold email generation completed.",
            payload={
                "lead_id": lead.id,
                "draft_id": stored.id,
                "subject": stored.subject,
                "category": score.grade,
            },
        )
        if notify_slack and score.grade == "HOT":
            approval_link = self.approvals.approval_link_for_draft(
                stored.id,
                base_url=self.settings.approval_base_url,
            )
            try:
                slack = self.slack_service or SlackNotificationService(settings=self.settings)
                slack.notify_hot_lead(lead=lead, score=score, draft_url=approval_link.approve_url)
            except Exception as exc:
                self.logs.record(
                    lead_id=lead.id,
                    event_type="slack.hot_lead_notification_failed",
                    status="failed",
                    message=str(exc),
                    payload={"draft_id": stored.id, "score_id": score.id},
                )
                logger.exception("slack_hot_lead_notification_failed", extra={"lead_id": lead.id})
            else:
                self.logs.record(
                    lead_id=lead.id,
                    event_type="slack.hot_lead_notification_sent",
                    status="completed",
                    message="Slack hot lead notification delivered.",
                    payload={
                        "draft_id": stored.id,
                        "score_id": score.id,
                        "approval_url": approval_link.approve_url,
                    },
                )

        return stored
