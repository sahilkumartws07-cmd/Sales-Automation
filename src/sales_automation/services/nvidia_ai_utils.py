from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from openai import OpenAI

from sales_automation.config import Settings, get_settings

logger = logging.getLogger(__name__)


class NvidiaAIUtility:
    def __init__(self, settings: Settings | None = None, client: OpenAI | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = client or OpenAI(
            api_key=self.settings.nvidia_api_key,
            base_url=self.settings.nvidia_base_url,
            timeout=self.settings.ai_timeout_seconds,
            max_retries=self.settings.ai_max_retries,
        )
        self.model = self.settings.nvidia_model

    def research_company(
        self, *, company_name: str, lead_title: str | None = None
    ) -> dict[str, Any]:
        prompt = (
            "Return JSON with keys summary, pain_points, signals, sources. "
            f"Company: {company_name}. Lead title: {lead_title or 'unknown'}."
        )
        return self._json_completion(prompt)

    def summarize_website(
        self,
        *,
        company_name: str,
        website_url: str,
        content: str,
    ) -> dict[str, Any]:
        prompt = (
            "Analyze this company website content and return JSON with keys "
            "summary, pain_points, signals. Keep the summary factual and useful for B2B sales. "
            f"Company: {company_name}. Website: {website_url}. Content: {content}"
        )
        return self._json_completion(prompt)

    def score_lead(
        self, *, lead: dict[str, Any], research: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        prompt = (
            "Return JSON with keys score (0-100), grade, rationale, factors. "
            f"Lead: {json.dumps(lead, default=str)}. "
            f"Research: {json.dumps(research or {}, default=str)}."
        )
        return self._json_completion(prompt)

    def score_lead_from_research(
        self,
        *,
        lead: dict[str, Any],
        company_summary: str,
        signals: list[dict[str, Any]] | None = None,
        pain_points: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        prompt = (
            "Score this sales lead from 1 to 10 using the company research summary. "
            "Categorize the lead as HOT, WARM, or COLD. "
            "HOT means strong fit and clear buying signals. "
            "WARM means plausible fit but incomplete urgency or evidence. "
            "COLD means weak fit, low relevance, or insufficient evidence. "
            f"Lead: {json.dumps(lead, default=str)}. "
            f"Company summary: {company_summary}. "
            f"Signals: {json.dumps(signals or [], default=str)}. "
            f"Pain points: {json.dumps(pain_points or [], default=str)}."
        )
        return self._json_schema_completion(prompt, schema=_LEAD_SCORE_SCHEMA)

    def draft_email(
        self,
        *,
        lead: dict[str, Any],
        research: dict[str, Any] | None = None,
        score: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        prompt = (
            "Return JSON with keys subject and body for a concise, personalized B2B sales email. "
            f"Lead: {json.dumps(lead, default=str)}. "
            f"Research: {json.dumps(research or {}, default=str)}. "
            f"Score: {json.dumps(score or {}, default=str)}."
        )
        result = self._json_completion(prompt)
        return {"subject": str(result["subject"]), "body": str(result["body"])}

    def generate_cold_email(
        self,
        *,
        lead: dict[str, Any],
        company_summary: str,
        lead_category: str,
    ) -> dict[str, Any]:
        prompt = (
            "Generate a professional, personalized one-to-one B2B outreach email for this sales lead. "
            "Use plain text, a natural human tone, and specific relevance from the company context. "
            "Create a clear subject line under 60 characters, an email body of 90-140 words, "
            "and one low-pressure call to action. "
            "Avoid hype, exaggerated urgency, misleading claims, spam-trigger phrases, emojis, "
            "all-caps wording, excessive punctuation, and generic mass-mail language. "
            f"Lead: {json.dumps(lead, default=str)}. "
            f"Company Summary: {company_summary}. "
            f"Lead Category: {lead_category}. "
            "Tailor specificity and confidence based on lead category "
            "(HOT = direct but respectful, WARM = balanced, COLD = exploratory)."
        )
        return self._json_schema_completion(prompt, schema=_COLD_EMAIL_SCHEMA)

    def classify_email_reply(
        self,
        *,
        from_email: str,
        subject: str | None,
        body: str,
    ) -> dict[str, Any]:
        prompt = (
            "Classify this sales outreach reply. Use one of these labels exactly: "
            "INTERESTED, NOT_INTERESTED, OUT_OF_OFFICE, NEEDS_FOLLOW_UP. "
            "Return a short reason and whether a human should review it urgently. "
            f"From: {from_email}. Subject: {subject or ''}. Body: {body}"
        )
        return self._json_schema_completion(prompt, schema=_EMAIL_REPLY_CLASSIFICATION_SCHEMA)

    def _json_completion(self, prompt: str) -> dict[str, Any]:
        logger.info("nvidia_request_started", extra={"model": self.model})
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a sales automation assistant. Return valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=300,
            temperature=0.1,
        )
        text = response.choices[0].message.content
        logger.info("nvidia_request_completed", extra={"model": self.model})
        return json.loads(text)

    def _json_schema_completion(self, prompt: str, *, schema: Mapping[str, Any]) -> dict[str, Any]:
        logger.info("nvidia_structured_request_started", extra={"model": self.model})
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a sales lead scoring assistant. "
                        "Return only data that matches the requested JSON schema."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "lead_score",
                    "schema": schema,
                    "strict": True,
                },
            },
            max_tokens=300,
            temperature=0.1,
        )
        logger.info("nvidia_structured_request_completed", extra={"model": self.model})
        return json.loads(response.choices[0].message.content)


_LEAD_SCORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "score": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10,
            "description": "Lead score from 1 to 10.",
        },
        "category": {
            "type": "string",
            "enum": ["HOT", "WARM", "COLD"],
            "description": "Sales priority category.",
        },
        "reason": {
            "type": "string",
            "description": "Concise explanation for the score and category.",
        },
        "factors": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "fit": {"type": "string"},
                "urgency": {"type": "string"},
                "evidence": {"type": "string"},
            },
            "required": ["fit", "urgency", "evidence"],
            "description": "Relevant scoring factors as short string values.",
        },
    },
    "required": ["score", "category", "reason", "factors"],
}

_COLD_EMAIL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "subject": {
            "type": "string",
            "maxLength": 100,
            "description": "Compelling email subject line (under 60 characters recommended).",
        },
        "body": {
            "type": "string",
            "description": "Personalized B2B sales email body (2-3 paragraphs, professional tone).",
        },
        "call_to_action": {
            "type": "string",
            "description": "Clear and specific call to action (e.g., 'Schedule a 15-minute call', 'Reply with your thoughts').",
        },
    },
    "required": ["subject", "body", "call_to_action"],
}

_EMAIL_REPLY_CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "classification": {
            "type": "string",
            "enum": ["INTERESTED", "NOT_INTERESTED", "OUT_OF_OFFICE", "NEEDS_FOLLOW_UP"],
            "description": "The sales-reply classification label.",
        },
        "reason": {
            "type": "string",
            "description": "Concise reason for the classification.",
        },
        "requires_human_review": {
            "type": "boolean",
            "description": "True when a salesperson should review or respond promptly.",
        },
    },
    "required": ["classification", "reason", "requires_human_review"],
}
