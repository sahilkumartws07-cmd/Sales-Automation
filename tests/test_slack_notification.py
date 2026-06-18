from __future__ import annotations

from datetime import UTC, datetime
import json

import pytest

from sales_automation.config import Settings
from sales_automation.models import EmailDraft, EmailReply, Lead, LeadScore
from sales_automation.services.email_approval import EmailApprovalService
from sales_automation.services.reply_classification import ReplyClassificationService
from sales_automation.services.slack_notification import SlackNotificationService


class FakeResponse:
    def __init__(self) -> None:
        self.raise_for_status_called = False

    def raise_for_status(self) -> None:
        self.raise_for_status_called = True


class FakeHttpClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.response = FakeResponse()

    def post(self, url: str, *, json: dict[str, object], timeout: int) -> FakeResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return self.response


def test_notify_hot_lead_posts_slack_payload_with_approval_url() -> None:
    http_client = FakeHttpClient()
    service = SlackNotificationService(
        webhook_url="https://hooks.slack.test/services/T/B/C",
        http_client=http_client,  # type: ignore[arg-type]
    )
    lead = Lead(
        id=1,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        company_name="Example Co",
    )
    score = LeadScore(id=2, lead_id=1, score=9, grade="HOT", rationale="Strong ICP fit.")

    service.notify_hot_lead(
        lead=lead,
        score=score,
        draft_url="http://localhost:8000/approvals/1/approve",
    )

    assert len(http_client.calls) == 1
    call = http_client.calls[0]
    assert call["url"] == "https://hooks.slack.test/services/T/B/C"
    assert call["timeout"] == 10
    payload = call["json"]
    assert payload["text"] == "Hot lead: Example Co (9/10, HOT). Reason: Strong ICP fit."
    assert "http://localhost:8000/approvals/1/approve" in json.dumps(payload["blocks"])
    assert http_client.response.raise_for_status_called


def test_notify_interested_reply_posts_slack_payload() -> None:
    http_client = FakeHttpClient()
    service = SlackNotificationService(
        webhook_url="https://hooks.slack.test/services/T/B/C",
        http_client=http_client,  # type: ignore[arg-type]
    )
    lead = Lead(
        id=1,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        company_name="Example Co",
    )
    reply = EmailReply(
        lead_id=1,
        from_email="ada@example.com",
        subject="Re: quick question",
        body="Interested",
        sentiment="INTERESTED",
        received_at=datetime.now(UTC),
    )

    service.notify_interested_reply(lead=lead, reply=reply)

    payload = http_client.calls[0]["json"]
    assert payload["text"] == "Interested reply from Example Co: Re: quick question"
    assert "ada@example.com" in json.dumps(payload["blocks"])
    assert "Re: quick question" in json.dumps(payload["blocks"])


def test_notify_email_sent_posts_slack_payload() -> None:
    http_client = FakeHttpClient()
    service = SlackNotificationService(
        webhook_url="https://hooks.slack.test/services/T/B/C",
        http_client=http_client,  # type: ignore[arg-type]
    )
    lead = Lead(
        id=1,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        company_name="Example Co",
    )
    draft = EmailDraft(
        id=2,
        lead_id=1,
        subject="Relevant question",
        body="Hi Ada",
        status="sent",
    )

    service.notify_email_sent(lead=lead, draft=draft, message_id="gmail-123")

    payload = http_client.calls[0]["json"]
    assert payload["text"] == "Email sent to Ada Lovelace at Example Co: Relevant question"
    assert "ada@example.com" in json.dumps(payload["blocks"])
    assert "gmail-123" in json.dumps(payload["blocks"])
    assert http_client.response.raise_for_status_called


def test_send_approved_notifies_slack_after_gmail_send() -> None:
    lead = Lead(
        id=1,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        company_name="Example Co",
    )
    draft = EmailDraft(
        id=2,
        lead_id=1,
        subject="Relevant question",
        body="Hi Ada",
        status="approved",
    )
    draft.lead = lead

    class FakeDrafts:
        def get(self, draft_id: int) -> EmailDraft | None:
            assert draft_id == 2
            return draft

    class FakeGmail:
        def send_draft(self, *, lead: Lead, draft: EmailDraft) -> str:
            return "gmail-123"

    class FakeLogs:
        def __init__(self) -> None:
            self.records: list[dict[str, object]] = []

        def record(self, **kwargs: object) -> None:
            self.records.append(kwargs)

    class FakeSlack:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def notify_email_sent(
            self,
            *,
            lead: Lead,
            draft: EmailDraft,
            message_id: str | None = None,
        ) -> None:
            self.calls.append({"lead": lead, "draft": draft, "message_id": message_id})

    logs = FakeLogs()
    slack = FakeSlack()
    service = EmailApprovalService.__new__(EmailApprovalService)
    service.drafts = FakeDrafts()  # type: ignore[assignment]
    service.logs = logs  # type: ignore[assignment]
    service.slack_service = slack  # type: ignore[assignment]

    sent = service.send_approved(2, gmail_service=FakeGmail())  # type: ignore[arg-type]

    assert sent.status == "sent"
    assert slack.calls == [{"lead": lead, "draft": draft, "message_id": "gmail-123"}]
    assert any(
        record["event_type"] == "slack.email_sent_notification_sent"
        for record in logs.records
    )


def test_send_response_to_reply_sends_threaded_gmail_reply() -> None:
    reply = EmailReply(
        id=3,
        lead_id=1,
        from_email="Ada Lovelace <ada@example.com>",
        subject="Re: quick question",
        body="Interested",
        sentiment="INTERESTED",
        gmail_thread_id="thread-123",
        gmail_rfc_message_id="<message-1@example.com>",
        received_at=datetime.now(UTC),
    )

    class FakeReplies:
        def get(self, reply_id: int) -> EmailReply | None:
            assert reply_id == 3
            return reply

    class FakeGmail:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def send_reply(
            self,
            *,
            to_email: str,
            subject: str | None,
            body: str,
            thread_id: str | None = None,
            in_reply_to: str | None = None,
        ) -> str:
            self.calls.append(
                {
                    "to_email": to_email,
                    "subject": subject,
                    "body": body,
                    "thread_id": thread_id,
                    "in_reply_to": in_reply_to,
                }
            )
            return "gmail-sent-123"

    class FakeLogs:
        def __init__(self) -> None:
            self.records: list[dict[str, object]] = []

        def record(self, **kwargs: object) -> None:
            self.records.append(kwargs)

    class FakeSession:
        def __init__(self) -> None:
            self.commits = 0

        def commit(self) -> None:
            self.commits += 1

    class FakeSentMessages:
        def __init__(self) -> None:
            self.messages: list[object] = []

        def add(self, message: object) -> object:
            self.messages.append(message)
            return message

    gmail = FakeGmail()
    logs = FakeLogs()
    session = FakeSession()
    sent_messages = FakeSentMessages()
    service = ReplyClassificationService.__new__(ReplyClassificationService)
    service.replies = FakeReplies()  # type: ignore[assignment]
    service.sent_messages = sent_messages  # type: ignore[assignment]
    service.logs = logs  # type: ignore[assignment]
    service.session = session  # type: ignore[assignment]

    message_id = service.send_response_to_reply(
        3,
        body="Thanks Ada, here is my calendar link.",
        gmail_service=gmail,  # type: ignore[arg-type]
    )

    assert message_id == "gmail-sent-123"
    assert gmail.calls == [
        {
            "to_email": "ada@example.com",
            "subject": "Re: quick question",
            "body": "Thanks Ada, here is my calendar link.",
            "thread_id": "thread-123",
            "in_reply_to": "<message-1@example.com>",
        }
    ]
    assert session.commits == 1
    assert len(sent_messages.messages) == 1
    assert sent_messages.messages[0].email_draft_id is None
    assert sent_messages.messages[0].email_reply_id == 3
    assert sent_messages.messages[0].body == "Thanks Ada, here is my calendar link."
    assert logs.records[0]["event_type"] == "email_reply.response_sent"


def test_send_response_to_reply_without_thread_metadata_sends_unthreaded_email() -> None:
    reply = EmailReply(
        id=3,
        lead_id=1,
        from_email="ada@example.com",
        subject="Re: quick question",
        body="Interested",
        sentiment="INTERESTED",
        received_at=datetime.now(UTC),
    )

    class FakeReplies:
        def get(self, reply_id: int) -> EmailReply | None:
            assert reply_id == 3
            return reply

    class FakeGmail:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def send_reply(
            self,
            *,
            to_email: str,
            subject: str | None,
            body: str,
            thread_id: str | None = None,
            in_reply_to: str | None = None,
        ) -> str:
            self.calls.append(
                {
                    "to_email": to_email,
                    "subject": subject,
                    "body": body,
                    "thread_id": thread_id,
                    "in_reply_to": in_reply_to,
                }
            )
            return "gmail-sent-456"

    class FakeLogs:
        def __init__(self) -> None:
            self.records: list[dict[str, object]] = []

        def record(self, **kwargs: object) -> None:
            self.records.append(kwargs)

    class FakeSession:
        def commit(self) -> None:
            pass

    class FakeSentMessages:
        def __init__(self) -> None:
            self.messages: list[object] = []

        def add(self, message: object) -> object:
            self.messages.append(message)
            return message

    gmail = FakeGmail()
    logs = FakeLogs()
    sent_messages = FakeSentMessages()
    service = ReplyClassificationService.__new__(ReplyClassificationService)
    service.replies = FakeReplies()  # type: ignore[assignment]
    service.sent_messages = sent_messages  # type: ignore[assignment]
    service.logs = logs  # type: ignore[assignment]
    service.session = FakeSession()  # type: ignore[assignment]

    message_id = service.send_response_to_reply(
        3,
        body="Thanks",
        gmail_service=gmail,  # type: ignore[arg-type]
    )

    assert message_id == "gmail-sent-456"
    assert gmail.calls == [
        {
            "to_email": "ada@example.com",
            "subject": "Re: quick question",
            "body": "Thanks",
            "thread_id": None,
            "in_reply_to": None,
        }
    ]
    assert len(sent_messages.messages) == 1
    assert sent_messages.messages[0].body == "Thanks"
    assert logs.records[0]["payload"]["threaded"] is False


def test_post_requires_configured_webhook() -> None:
    service = SlackNotificationService(settings=Settings(DATABASE_URL="sqlite://"))
    service.webhook_url = None

    with pytest.raises(ValueError, match="SLACK_WEBHOOK_URL is not configured"):
        service.notify_hot_lead(
            lead=Lead(
                id=1,
                first_name="Ada",
                last_name="Lovelace",
                email="ada@example.com",
                company_name="Example Co",
            ),
            score=LeadScore(id=2, lead_id=1, score=9, grade="HOT", rationale="Strong ICP fit."),
        )
