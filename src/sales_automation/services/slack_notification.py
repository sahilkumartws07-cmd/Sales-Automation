from __future__ import annotations

from typing import Any

import requests

from sales_automation.config import Settings, get_settings
from sales_automation.models import EmailDraft, EmailReply, Lead, LeadScore


class SlackNotificationService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_client: requests.Session | None = None,
        webhook_url: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.http_client = http_client or requests.Session()
        self.webhook_url = webhook_url or self.settings.slack_webhook_url

    def notify_hot_lead(
        self,
        *,
        lead: Lead,
        score: LeadScore,
        draft_url: str | None = None,
    ) -> None:
        text = (
            f"Hot lead: {lead.company_name} ({score.score}/10, {score.grade}). "
            f"Reason: {score.rationale}"
        )
        payload: dict[str, Any] = {
            "text": text,
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Hot lead:* {lead.company_name}"},
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Contact:*\n{lead.first_name} {lead.last_name}",
                        },
                        {"type": "mrkdwn", "text": f"*Score:*\n{score.score}/10 ({score.grade})"},
                    ],
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Reason:*\n{score.rationale}"},
                },
            ],
        }
        if draft_url:
            payload["blocks"].append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Draft approval:*\n{draft_url}"},
                }
            )
        self._post(payload)

    def notify_interested_reply(self, *, lead: Lead, reply: EmailReply) -> None:
        payload = {
            "text": f"Interested reply from {lead.company_name}: {reply.subject or '(no subject)'}",
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Interested reply:* {lead.company_name}"},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*From:*\n{reply.from_email}"},
                        {
                            "type": "mrkdwn",
                            "text": f"*Subject:*\n{reply.subject or '(no subject)'}",
                        },
                    ],
                },
            ],
        }
        self._post(payload)

    def notify_email_sent(
        self,
        *,
        lead: Lead,
        draft: EmailDraft,
        message_id: str | None = None,
    ) -> None:
        recipient = f"{lead.first_name} {lead.last_name}".strip() or lead.email
        text = f"Email sent to {recipient} at {lead.company_name}: {draft.subject}"
        fields = [
            {"type": "mrkdwn", "text": f"*Recipient:*\n{recipient}"},
            {"type": "mrkdwn", "text": f"*Email:*\n{lead.email}"},
            {"type": "mrkdwn", "text": f"*Company:*\n{lead.company_name}"},
            {"type": "mrkdwn", "text": f"*Draft ID:*\n{draft.id}"},
        ]
        if message_id:
            fields.append({"type": "mrkdwn", "text": f"*Gmail message ID:*\n{message_id}"})

        payload = {
            "text": text,
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Email sent:* {draft.subject}"},
                },
                {"type": "section", "fields": fields},
            ],
        }
        self._post(payload)

    def _post(self, payload: dict[str, Any]) -> None:
        if not self.webhook_url:
            raise ValueError("SLACK_WEBHOOK_URL is not configured")
        response = self.http_client.post(self.webhook_url, json=payload, timeout=10)
        response.raise_for_status()
