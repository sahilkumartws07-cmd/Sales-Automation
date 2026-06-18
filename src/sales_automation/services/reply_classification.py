from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parseaddr
import logging
import re
from time import monotonic
from typing import Any

from sqlalchemy.orm import Session

from sales_automation.models import EmailReply, EmailSentMessage
from sales_automation.repositories import (
    EmailDraftRepository,
    EmailReplyRepository,
    EmailSentMessageRepository,
    LeadRepository,
    WorkflowLogRepository,
)
from sales_automation.services.gmail_service import GmailReplyMessage, GmailService, clean_reply_body
from sales_automation.services.openai_service import OpenAIService
from sales_automation.services.slack_notification import SlackNotificationService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplyClassificationResult:
    classified: int
    skipped: int
    failed: int
    errors: list[dict[str, Any]] = field(default_factory=list)


class ReplyClassificationService:
    def __init__(
        self,
        session: Session,
        *,
        openai_service: OpenAIService | None = None,
        gmail_service: GmailService | None = None,
        slack_service: SlackNotificationService | None = None,
    ) -> None:
        self.session = session
        self.openai_service = openai_service or OpenAIService()
        self.gmail_service = gmail_service
        self.slack_service = slack_service
        self.leads = LeadRepository(session)
        self.drafts = EmailDraftRepository(session)
        self.replies = EmailReplyRepository(session)
        self.sent_messages = EmailSentMessageRepository(session)
        self.logs = WorkflowLogRepository(session)

    def classify_gmail_replies(
        self,
        *,
        query: str = "is:unread",
        limit: int = 100,
        notify_slack: bool = False,
        max_seconds: float | None = None,
    ) -> ReplyClassificationResult:
        if self.gmail_service is None:
            self.gmail_service = GmailService()

        messages = self.gmail_service.list_unread_replies(query=query, limit=limit)
        classified = 0
        skipped = 0
        failed = 0
        errors: list[dict[str, Any]] = []
        started_at = monotonic()
        for message in messages:
            if max_seconds is not None and monotonic() - started_at >= max_seconds:
                break
            result = self.classify_messages([message], notify_slack=notify_slack)
            classified += result.classified
            skipped += result.skipped
            failed += result.failed
            errors.extend(result.errors)
            if result.classified:
                self.gmail_service.mark_read(message.message_id)
        return ReplyClassificationResult(
            classified=classified,
            skipped=skipped,
            failed=failed,
            errors=errors,
        )

    def classify_messages(
        self,
        messages: list[GmailReplyMessage],
        *,
        notify_slack: bool = False,
    ) -> ReplyClassificationResult:
        classified = 0
        skipped = 0
        failed = 0
        errors: list[dict[str, Any]] = []

        for message in messages:
            try:
                lead = self.leads.get_by_email(_email_address(message.from_email))
                if lead is None:
                    skipped += 1
                    errors.append({"message_id": message.message_id, "reason": "No matching lead"})
                    continue

                draft = self.drafts.get_by_lead_id(lead.id)
                reply = self.replies.add(
                    EmailReply(
                        lead_id=lead.id,
                        email_draft_id=draft.id if draft else None,
                        from_email=message.from_email,
                        subject=message.subject,
                        body=message.body,
                        sentiment=None,
                        gmail_message_id=message.message_id,
                        gmail_thread_id=message.thread_id,
                        gmail_rfc_message_id=message.rfc_message_id,
                        received_at=datetime.now(UTC),
                    )
                )
                classification = self._classify_reply(reply)
                reply.sentiment = classification["classification"]
                self.logs.record(
                    lead_id=lead.id,
                    event_type="email_reply.classified",
                    status="completed",
                    message="Email reply classified.",
                    payload={
                        "reply_id": reply.id,
                        "classification": reply.sentiment,
                        "reason": classification["reason"],
                    },
                )
                if notify_slack and reply.sentiment == "INTERESTED":
                    self._notify_interested_reply(lead=lead, reply=reply)
                self.session.commit()
                classified += 1
            except Exception as exc:
                self.session.rollback()
                failed += 1
                errors.append({"message_id": message.message_id, "error": str(exc)})
                logger.exception(
                    "reply_classification_failed",
                    extra={"message_id": message.message_id},
                )

        return ReplyClassificationResult(
            classified=classified,
            skipped=skipped,
            failed=failed,
            errors=errors,
        )

    def classify_unclassified_replies(
        self,
        *,
        limit: int = 100,
        notify_slack: bool = False,
        max_seconds: float | None = None,
    ) -> ReplyClassificationResult:
        classified = 0
        failed = 0
        errors: list[dict[str, Any]] = []
        started_at = monotonic()
        for reply in self.replies.list_unclassified(limit=limit):
            if max_seconds is not None and monotonic() - started_at >= max_seconds:
                break

            reply_id = reply.id
            try:
                classification = self._classify_reply(reply)
                reply.sentiment = classification["classification"]
                self.logs.record(
                    lead_id=reply.lead_id,
                    event_type="email_reply.classified",
                    status="completed",
                    message="Email reply classified.",
                    payload={
                        "reply_id": reply_id,
                        "classification": reply.sentiment,
                        "reason": classification["reason"],
                    },
                )
                if notify_slack and reply.sentiment == "INTERESTED":
                    self._notify_interested_reply(lead=reply.lead, reply=reply)
                self.session.commit()
                classified += 1
            except Exception as exc:
                self.session.rollback()
                failed += 1
                errors.append({"reply_id": reply_id, "error": str(exc)})
                logger.exception("reply_classification_failed", extra={"reply_id": reply_id})
        return ReplyClassificationResult(
            classified=classified,
            skipped=0,
            failed=failed,
            errors=errors,
        )

    def _classify_reply(self, reply: EmailReply) -> dict[str, Any]:
        body = clean_reply_body(reply.body or "")
        deterministic = classify_obvious_reply_intent(body)
        if deterministic is not None:
            return deterministic

        classification = self.openai_service.classify_email_reply(
            from_email=reply.from_email,
            subject=reply.subject,
            body=body,
        )
        return _normalize_ai_classification(classification=classification, body=body)

    def send_response_to_reply(
        self,
        reply_id: int,
        *,
        body: str,
        gmail_service: GmailService | None = None,
    ) -> str:
        reply = self.replies.get(reply_id)
        if reply is None:
            raise ValueError(f"Email reply not found: {reply_id}")
        if not body.strip():
            raise ValueError("Reply body cannot be empty")
        to_email = _email_address(reply.from_email) or reply.from_email.strip()
        if not to_email:
            raise ValueError("Reply sender email is missing")

        gmail = gmail_service or GmailService()
        message_id = gmail.send_reply(
            to_email=to_email,
            subject=reply.subject,
            body=body,
            thread_id=reply.gmail_thread_id,
            in_reply_to=reply.gmail_rfc_message_id,
        )
        self.sent_messages.add(
            EmailSentMessage(
                lead_id=reply.lead_id,
                email_draft_id=reply.email_draft_id,
                email_reply_id=reply.id,
                to_email=to_email,
                subject=reply.subject,
                body=body.strip(),
                gmail_message_id=message_id,
                gmail_thread_id=reply.gmail_thread_id,
                sent_at=datetime.now(UTC),
            )
        )
        self.logs.record(
            lead_id=reply.lead_id,
            event_type="email_reply.response_sent",
            status="completed",
            message="Response sent to email reply.",
            payload={
                "reply_id": reply.id,
                "message_id": message_id,
                "threaded": bool(reply.gmail_thread_id),
            },
        )
        self.session.commit()
        return message_id

    def _notify_interested_reply(self, *, lead: Any, reply: EmailReply) -> None:
        try:
            if self.slack_service is None:
                self.slack_service = SlackNotificationService()
            self.slack_service.notify_interested_reply(lead=lead, reply=reply)
        except Exception as exc:
            self.logs.record(
                lead_id=reply.lead_id,
                event_type="slack.interested_reply_notification_failed",
                status="failed",
                message=str(exc),
                payload={"reply_id": reply.id},
            )
            logger.exception(
                "slack_interested_reply_notification_failed",
                extra={"reply_id": reply.id, "lead_id": reply.lead_id},
            )
            return

        self.logs.record(
            lead_id=reply.lead_id,
            event_type="slack.interested_reply_notification_sent",
            status="completed",
            message="Slack interested reply notification delivered.",
            payload={"reply_id": reply.id},
        )


def _email_address(value: str) -> str:
    return parseaddr(value)[1].lower()


def classify_obvious_reply_intent(body: str) -> dict[str, Any] | None:
    return _rule_based_reply_classification(body)


_POSITIVE_REPLY_PATTERNS = [
    r"\b(i'?m|i am|we are|we'?re|would be|am)\s+interested\b",
    r"\binterested\b",
    r"\byes\b",
    r"\bsounds?\s+good\b",
    r"\blet'?s\s+(talk|chat|connect|schedule)\b",
    r"\b(schedule|book|set up)\s+(a\s+)?(call|meeting|demo)\b",
    r"\b(call|meeting|demo)\b",
    r"\bbrief\s+call\b",
    r"\btell\s+me\s+more\b",
    r"\blearn\s+more\b",
    r"\bshare\s+(more|details|info|information)\b",
    r"\bhappy\s+to\s+(talk|chat|connect|meet|discuss)\b",
    r"\b(open|available)\s+to\s+(talk|chat|connect|meet|discuss)\b",
]

_NEGATIVE_REPLY_PATTERNS = [
    r"\bnot\s+interested\b",
    r"\bno\s+(thanks|thank\s+you|interest)\b",
    r"\bnot\s+a\s+(fit|good\s+fit|priority)\b",
    r"\bremove\s+me\b",
    r"\bunsubscribe\b",
    r"\bstop\s+(emailing|contacting)\b",
    r"\bdon'?t\s+(email|contact|reach\s+out)\b",
]

_OUT_OF_OFFICE_PATTERNS = [
    r"\bout\s+of\s+office\b",
    r"\bautomatic\s+reply\b",
    r"\bauto[-\s]?reply\b",
    r"\baway\s+from\s+(the\s+)?office\b",
]


def _rule_based_reply_classification(body: str) -> dict[str, Any] | None:
    text = _normalized_reply_text(body)
    if not text:
        return None
    if _matches_any(text, _OUT_OF_OFFICE_PATTERNS):
        return {
            "classification": "OUT_OF_OFFICE",
            "reason": "Reply appears to be an out-of-office or automatic response.",
            "requires_human_review": False,
        }
    if _matches_any(text, _NEGATIVE_REPLY_PATTERNS):
        return {
            "classification": "NOT_INTERESTED",
            "reason": "Reply contains an explicit negative intent signal.",
            "requires_human_review": False,
        }
    if _matches_any(text, _POSITIVE_REPLY_PATTERNS):
        return {
            "classification": "INTERESTED",
            "reason": "Reply contains an explicit positive intent signal.",
            "requires_human_review": True,
        }
    return None


def _normalize_ai_classification(*, classification: dict[str, Any], body: str) -> dict[str, Any]:
    normalized = dict(classification)
    label = _canonical_reply_classification(normalized.get("classification"))
    if label == "NOT_INTERESTED":
        positive = _rule_based_reply_classification(body)
        if positive and positive["classification"] == "INTERESTED":
            return {
                **positive,
                "reason": "Positive reply text overrode an AI not-interested classification.",
            }
    allowed = {"INTERESTED", "NOT_INTERESTED", "OUT_OF_OFFICE", "NEEDS_FOLLOW_UP"}
    normalized["classification"] = label if label in allowed else "NEEDS_FOLLOW_UP"
    normalized.setdefault("reason", "Reply requires follow-up review.")
    normalized.setdefault("requires_human_review", normalized["classification"] in {"INTERESTED", "NEEDS_FOLLOW_UP"})
    return normalized


def _canonical_reply_classification(value: Any) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip()).strip("_").upper()
    aliases = {
        "INTERESTED": "INTERESTED",
        "NOT_INTERESTED": "NOT_INTERESTED",
        "NOTINTERESTED": "NOT_INTERESTED",
        "NO_INTEREST": "NOT_INTERESTED",
        "OUT_OF_OFFICE": "OUT_OF_OFFICE",
        "OOO": "OUT_OF_OFFICE",
        "NEEDS_FOLLOW_UP": "NEEDS_FOLLOW_UP",
        "FOLLOW_UP": "NEEDS_FOLLOW_UP",
    }
    return aliases.get(cleaned, cleaned)


def _normalized_reply_text(body: str) -> str:
    return " ".join(clean_reply_body(body).lower().split())


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)
