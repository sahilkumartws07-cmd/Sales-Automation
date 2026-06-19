from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import HTTPException

from sales_automation.api.main import (
    _error_response,
    _interested_reply_to_notification,
    _notification_cards,
    _notification_filters,
    _notification_summary,
    _repair_reply_sentiments,
    _reply_to_read,
    _save_uploaded_csv,
    _sent_conversation_for_draft,
    _slack_log_to_notification,
    _website_research_message,
    app,
    classify_replies,
    draft_action,
    respond_to_reply,
    score_leads,
)
from sales_automation.api.schemas import (
    ClassifyRepliesRequest,
    DraftActionRequest,
    ReplyResponseRequest,
    ScoreRequest,
)
from sales_automation.services.lead_scoring import LeadScoringResult
from sales_automation.services.reply_classification import ReplyClassificationResult


def test_static_lead_routes_are_registered_before_dynamic_lead_route() -> None:
    paths = [route.path for route in app.routes]

    assert paths.index("/leads/import") < paths.index("/leads/{lead_id}")
    assert paths.index("/leads/score") < paths.index("/leads/{lead_id}")
    assert "/notifications" in paths


def test_rest_api_routes_use_standard_http_methods() -> None:
    route_methods = {
        route.path: route.methods
        for route in app.routes
        if getattr(route, "path", "").startswith(
            (
                "/auth",
                "/drafts",
                "/emails",
                "/health",
                "/leads",
                "/logs",
                "/notifications",
                "/notify",
                "/replies",
                "/research",
                "/sent",
                "/sheets",
            )
        )
    }

    assert "GET" in route_methods["/health"]
    assert "GET" in route_methods["/leads"]
    assert "GET" in route_methods["/leads/{lead_id}"]
    assert "GET" in route_methods["/drafts"]
    assert "GET" in route_methods["/drafts/pending"]
    assert "GET" in route_methods["/sent"]
    assert "GET" in route_methods["/replies"]
    assert "GET" in route_methods["/notifications"]
    assert "GET" in route_methods["/logs"]

    assert "POST" in route_methods["/auth/register"]
    assert "POST" in route_methods["/auth/login"]
    assert "POST" in route_methods["/auth/refresh-token"]
    assert "POST" in route_methods["/auth/forgot-password"]
    assert "POST" in route_methods["/leads/import"]
    assert "POST" in route_methods["/emails/generate"]
    assert "POST" in route_methods["/replies/classify"]
    assert "POST" in route_methods["/replies/{reply_id}/respond"]
    assert "POST" in route_methods["/sheets/log/{lead_id}"]
    assert "POST" in route_methods["/notify/slack/test"]

    assert "POST" in route_methods["/auth/verify-otp"]
    assert "POST" in route_methods["/auth/reset-password"]
    assert "POST" in route_methods["/leads/score"]
    assert "POST" in route_methods["/research/websites"]
    assert "PUT" in route_methods["/drafts/{draft_id}"]
    assert "POST" in route_methods["/replies/classify-unclassified"]


def test_uploaded_csv_is_saved_to_media(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("sales_automation.api.main.MEDIA_DIR", tmp_path)

    path = _save_uploaded_csv("../bad/name?.txt", b"email,company\nada@example.com,Example\n")

    assert path.parent == tmp_path
    assert path.name.endswith("name_.txt.csv")
    assert path.read_bytes() == b"email,company\nada@example.com,Example\n"


def test_website_research_message_reports_failures_and_timeouts() -> None:
    assert (
        _website_research_message(
            requested_limit=100,
            effective_limit=100,
            processed=0,
            failed=1,
            timed_out=False,
        )
        == "Website research completed with 1 failed lead(s)."
    )
    assert (
        _website_research_message(
            requested_limit=100,
            effective_limit=100,
            processed=1,
            failed=0,
            timed_out=True,
        )
        == "Website research stopped at the API time limit. Call this endpoint again for more leads."
    )


def test_score_leads_returns_false_when_no_leads_available(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeLeadScoringService:
        def __init__(self, db: object, openai_service: object | None = None) -> None:
            calls.append({"method": "init", "db": db, "has_openai_service": openai_service is not None})

        def score_unscored_leads(
            self,
            *,
            limit: int,
            max_seconds: float | None = None,
        ) -> LeadScoringResult:
            calls.append({"method": "score", "limit": limit, "max_seconds": max_seconds})
            return LeadScoringResult(
                scored=0,
                skipped=0,
                failed=0,
                errors=[],
                status=False,
                message="No leads are available for scoring.",
            )

    class FakeDb:
        def commit(self) -> None:
            calls.append({"method": "commit"})

    db = FakeDb()
    monkeypatch.setattr("sales_automation.api.main.AILeadScoringService", FakeLeadScoringService)

    response = score_leads(body=ScoreRequest(limit=10), db=db)  # type: ignore[arg-type]

    assert response.model_dump() == {
        "status": False,
        "scored": 0,
        "skipped": 0,
        "failed": 0,
        "message": "No leads are available for scoring.",
    }
    assert calls == [
        {"method": "init", "db": db, "has_openai_service": True},
        {"method": "score", "limit": 10, "max_seconds": 45},
        {"method": "commit"},
    ]


def test_slack_log_notification_item_includes_related_lead() -> None:
    created_at = datetime(2026, 6, 18, 10, 15, tzinfo=UTC)
    log = SimpleNamespace(
        id=9,
        event_type="slack.interested_reply_notification_sent",
        status="completed",
        message="Slack interested reply notification delivered.",
        payload={"reply_id": 7},
        created_at=created_at,
        lead=SimpleNamespace(
            id=2,
            first_name="Sahil",
            last_name="Kumar",
            email="sahil@example.com",
            company_name="Aether AI",
        ),
    )

    item = _slack_log_to_notification(log)

    assert item.model_dump() == {
        "id": "slack-log-9",
        "type": "slack_notification",
        "category": "interested_reply",
        "status": "completed",
        "severity": "success",
        "channel": "#sales-alerts",
        "title": "Sahil Kumar (Aether AI) replied with classification: Interested",
        "message": "Sahil Kumar (Aether AI) replied with classification: Interested",
        "content": "Slack interested reply notification delivered.",
        "preview": "Slack interested reply notification delivered.",
        "badge_label": "Interested Reply",
        "badge_variant": "success",
        "sender_email": None,
        "recipient_email": None,
        "subject": None,
        "timestamp": created_at,
        "timestamp_iso": created_at.isoformat(),
        "display_time": created_at.strftime("%-I:%M %p"),
        "display_date": created_at.strftime("%b %-d, %Y, %-I:%M %p"),
        "display_datetime": created_at.strftime("%d/%m/%Y, %H:%M"),
        "lead": {
            "id": 2,
            "name": "Sahil Kumar",
            "email": "sahil@example.com",
            "company_name": "Aether AI",
        },
        "thread": {
            "reply_id": 7,
            "draft_id": None,
            "gmail_message_id": None,
            "gmail_thread_id": None,
        },
        "action": {"label": "Open Reply", "target": "/replies/7", "method": "POST"},
        "reply_id": 7,
        "draft_id": None,
        "gmail_message_id": None,
        "gmail_thread_id": None,
        "event_type": "slack.interested_reply_notification_sent",
        "payload": {"reply_id": 7},
    }


def test_interested_reply_notification_item_is_frontend_friendly(monkeypatch) -> None:
    monkeypatch.setattr(
        "sales_automation.api.main.settings.gmail_sender_email", "sales@example.com"
    )
    received_at = datetime(2026, 6, 18, 11, 0, tzinfo=UTC)
    reply = SimpleNamespace(
        id=7,
        lead_id=2,
        email_draft_id=3,
        from_email="Buyer <buyer@example.com>",
        subject="Re: Aether AI Growth",
        body="Yes, I am interested.\n\nOn earlier thread wrote:",
        sentiment="INTERESTED",
        received_at=received_at,
        updated_at=received_at,
        created_at=received_at,
        gmail_message_id="gmail-1",
        gmail_thread_id="thread-1",
        lead=SimpleNamespace(
            id=2,
            first_name="Sahil",
            last_name="Kumar",
            email="sahil@example.com",
            company_name="Aether AI",
        ),
        email_draft=SimpleNamespace(subject="Aether AI Growth"),
    )

    item = _interested_reply_to_notification(reply)

    assert item.type == "email_reply"
    assert item.category == "interested_reply"
    assert (
        item.title
        == "Sahil Kumar (Aether AI) replied with classification: Interested: Re: Aether AI Growth"
    )
    assert item.content == "Yes, I am interested."
    assert item.preview == "Yes, I am interested."
    assert item.badge_label == "Interested Reply"
    assert item.action is not None
    assert item.action.target == "/replies/7"
    assert item.sender_email == "Buyer <buyer@example.com>"
    assert item.recipient_email == "sales@example.com"
    assert item.lead is not None
    assert item.lead.company_name == "Aether AI"
    assert item.gmail_thread_id == "thread-1"


def test_notification_dashboard_summary_cards_and_filters() -> None:
    timestamp = datetime(2026, 6, 18, 10, 15, tzinfo=UTC)
    items = [
        _slack_log_to_notification(
            SimpleNamespace(
                id=1,
                event_type="slack.hot_lead_notification_sent",
                status="completed",
                message="Slack hot lead notification delivered.",
                payload={"draft_id": 10, "score": 9},
                created_at=timestamp,
                lead=SimpleNamespace(
                    id=2,
                    first_name="Robert",
                    last_name="Baratheon",
                    email="robert@example.com",
                    company_name="Ironworks Inc.",
                ),
            )
        ),
        _interested_reply_to_notification(
            SimpleNamespace(
                id=7,
                lead_id=2,
                email_draft_id=3,
                from_email="elena@example.com",
                subject="Re: Follow up",
                body="Are you free for a call?",
                sentiment="INTERESTED",
                received_at=timestamp,
                updated_at=timestamp,
                created_at=timestamp,
                gmail_message_id="gmail-1",
                gmail_thread_id="thread-1",
                lead=SimpleNamespace(
                    id=3,
                    first_name="Elena",
                    last_name="Rostova",
                    email="elena@example.com",
                    company_name="Finflow Solutions",
                ),
                email_draft=SimpleNamespace(subject="Follow up"),
            )
        ),
    ]

    assert _notification_summary(items).model_dump() == {
        "total_alerts": 2,
        "slack_feed_log": 1,
        "hot_alerts": 1,
        "replies_alert": 1,
        "system_warnings": 0,
    }
    assert [card.model_dump() for card in _notification_cards(items)] == [
        {
            "key": "slack_feed_log",
            "label": "Slack Feed Log",
            "value": 1,
            "display_value": "1 Alerts",
            "variant": "info",
        },
        {
            "key": "hot_alerts",
            "label": "Hot Alerts",
            "value": 1,
            "display_value": "1",
            "variant": "danger",
        },
        {
            "key": "replies_alert",
            "label": "Replies Alert",
            "value": 1,
            "display_value": "1",
            "variant": "success",
        },
        {
            "key": "system_warnings",
            "label": "System Warnings",
            "value": 0,
            "display_value": "0",
            "variant": "warning",
        },
    ]
    assert [filter_item.model_dump() for filter_item in _notification_filters(items)] == [
        {"key": "all_alerts", "label": "All Alerts", "count": 2, "active": True},
        {"key": "hot_leads", "label": "HOT Leads", "count": 1, "active": False},
        {"key": "replies", "label": "Replies", "count": 1, "active": False},
        {"key": "system_warnings", "label": "System Warnings", "count": 0, "active": False},
    ]


def test_reply_api_read_model_returns_clean_body(monkeypatch) -> None:
    monkeypatch.setattr("sales_automation.api.main.settings.gmail_sender_email", None)
    now = datetime.now(UTC)
    reply = SimpleNamespace(
        id=1,
        lead_id=2,
        email_draft_id=3,
        from_email="Sahil Kumar <sahil@example.com>",
        lead=SimpleNamespace(
            first_name="Sahil",
            last_name="Kumar",
            email="sahil@example.com",
            company_name="Aether AI",
        ),
        subject="Re: Aether AI Growth",
        body=(
            "Yes, I would be interested.\n\n"
            "On Tue, Jun 16, 2026 at 6:13 PM Sahil Kumar <sahil@example.com> wrote:\n"
            "> Yes, I would be interested.\n"
            "> Schedule a call\n"
        ),
        sentiment="INTERESTED",
        gmail_thread_id="thread-123",
        received_at=now,
        created_at=now,
        updated_at=now,
        email_draft=SimpleNamespace(
            id=3,
            subject="Aether AI Growth",
            body="Original sent email body",
            updated_at=now,
        ),
        sent_messages=[],
    )

    response = _reply_to_read(reply)

    assert response.model_dump() == {
        "id": 1,
        "lead_id": 2,
        "email_draft_id": 3,
        "from_email": "Sahil Kumar <sahil@example.com>",
        "sender_name": "Sahil Kumar",
        "company_name": "Aether AI",
        "subject": "Re: Aether AI Growth",
        "body": "Yes, I would be interested.",
        "preview": "Yes, I would be interested.",
        "sentiment": "INTERESTED",
        "status_label": "Interested",
        "can_reply": True,
        "date": now,
        "timestamp": now,
        "display_date": now.strftime("%b %-d, %Y, %-I:%M %p"),
        "received_at": now,
        "created_at": now,
        "updated_at": now,
        "original_subject": "Aether AI Growth",
        "original_body": "Original sent email body",
        "thread_body": (
            f"OUTBOUND {now.isoformat()}\n"
            "Subject: Aether AI Growth\n\n"
            "Original sent email body\n\n"
            f"INBOUND {now.isoformat()}\n"
            "Subject: Re: Aether AI Growth\n\n"
            "Yes, I would be interested."
        ),
        "messages": [
            {
                "id": "draft-3",
                "direction": "outbound",
                "from_email": None,
                "to_email": "sahil@example.com",
                "subject": "Aether AI Growth",
                "body": "Original sent email body",
                "sent_at": now,
                "reply_id": None,
                "gmail_message_id": None,
                "gmail_thread_id": None,
            },
            {
                "id": "reply-1",
                "direction": "inbound",
                "from_email": "Sahil Kumar <sahil@example.com>",
                "to_email": None,
                "subject": "Re: Aether AI Growth",
                "body": "Yes, I would be interested.",
                "sent_at": now,
                "reply_id": 1,
                "gmail_message_id": None,
                "gmail_thread_id": "thread-123",
            },
        ],
    }


def test_reply_api_read_model_falls_back_from_invalid_date(monkeypatch) -> None:
    monkeypatch.setattr("sales_automation.api.main.settings.gmail_sender_email", None)
    fallback = datetime(2026, 6, 18, 9, 30, tzinfo=UTC)
    reply = SimpleNamespace(
        id=1,
        lead_id=2,
        email_draft_id=None,
        from_email="sahil@example.com",
        lead=SimpleNamespace(company_name="Aether AI", first_name="Sahil", last_name="Kumar"),
        subject="Re: Aether AI Growth",
        body="Testing",
        sentiment=None,
        gmail_thread_id=None,
        received_at="Invalid Date",
        created_at=fallback,
        updated_at=fallback,
        email_draft=None,
        sent_messages=[],
    )

    response = _reply_to_read(reply)

    assert response.date == fallback
    assert response.timestamp == fallback
    assert response.display_date == fallback.strftime("%b %-d, %Y, %-I:%M %p")


def test_reply_api_read_model_corrects_stale_not_interested_positive_reply(monkeypatch) -> None:
    monkeypatch.setattr("sales_automation.api.main.settings.gmail_sender_email", None)
    now = datetime(2026, 6, 18, 10, 0, tzinfo=UTC)
    reply = SimpleNamespace(
        id=1,
        lead_id=2,
        email_draft_id=None,
        from_email="buyer@example.com",
        lead=SimpleNamespace(company_name="Aether AI", first_name="Sahil", last_name="Kumar"),
        subject="Re: Exploring AI Solutions",
        body="Yes, I would be interested.",
        sentiment="NOT_INTERESTED",
        gmail_thread_id=None,
        received_at=now,
        created_at=now,
        updated_at=now,
        email_draft=None,
        sent_messages=[],
    )

    response = _reply_to_read(reply)

    assert response.sentiment == "INTERESTED"
    assert response.status_label == "Interested"


def test_reply_api_read_model_corrects_display_variant_not_interested_positive_reply(
    monkeypatch,
) -> None:
    monkeypatch.setattr("sales_automation.api.main.settings.gmail_sender_email", None)
    now = datetime(2026, 6, 18, 10, 0, tzinfo=UTC)
    reply = SimpleNamespace(
        id=1,
        lead_id=2,
        email_draft_id=None,
        from_email="buyer@example.com",
        lead=SimpleNamespace(company_name="Aether AI", first_name="Sahil", last_name="Kumar"),
        subject="Re: Exploring AI Solutions",
        body="Yes, I would be interested.",
        sentiment="Not Interested",
        gmail_thread_id=None,
        received_at=now,
        created_at=now,
        updated_at=now,
        email_draft=None,
        sent_messages=[],
    )

    response = _reply_to_read(reply)

    assert response.sentiment == "INTERESTED"
    assert response.status_label == "Interested"
    assert _repair_reply_sentiments([reply], [response]) is True
    assert reply.sentiment == "INTERESTED"


def test_reply_api_read_model_corrects_stale_interested_negative_reply(monkeypatch) -> None:
    monkeypatch.setattr("sales_automation.api.main.settings.gmail_sender_email", None)
    now = datetime(2026, 6, 18, 10, 0, tzinfo=UTC)
    reply = SimpleNamespace(
        id=1,
        lead_id=2,
        email_draft_id=None,
        from_email="buyer@example.com",
        lead=SimpleNamespace(company_name="Aether AI", first_name="Sahil", last_name="Kumar"),
        subject="Re: Exploring AI Solutions",
        body="Thanks, but we are not interested.",
        sentiment="INTERESTED",
        gmail_thread_id=None,
        received_at=now,
        created_at=now,
        updated_at=now,
        email_draft=None,
        sent_messages=[],
    )

    response = _reply_to_read(reply)

    assert response.sentiment == "NOT_INTERESTED"
    assert response.status_label == "Not Interested"
    assert _repair_reply_sentiments([reply], [response]) is True
    assert reply.sentiment == "NOT_INTERESTED"


def test_sent_conversation_includes_draft_replies_and_sent_followups(monkeypatch) -> None:
    first_sent_at = datetime(2026, 6, 17, 10, 0, tzinfo=UTC)
    reply_at = datetime(2026, 6, 17, 11, 0, tzinfo=UTC)
    followup_at = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    lead = SimpleNamespace(
        id=2,
        first_name="Sahil",
        last_name="Kumar",
        email="sahil@example.com",
        company_name="Aether AI",
    )
    draft = SimpleNamespace(
        id=3,
        lead_id=2,
        lead=lead,
        subject="Aether AI Growth",
        body="Original approved email",
        updated_at=first_sent_at,
    )
    inbound_reply = SimpleNamespace(
        id=4,
        from_email="Sahil Kumar <sahil@example.com>",
        subject="Re: Aether AI Growth",
        body="Interested",
        received_at=reply_at,
        gmail_message_id="gmail-reply-1",
        gmail_thread_id="thread-1",
    )
    sent_followup = SimpleNamespace(
        id=5,
        to_email="sahil@example.com",
        subject="Re: Aether AI Growth",
        body="Great, here is my calendar link.",
        sent_at=followup_at,
        email_reply_id=4,
        gmail_message_id="gmail-sent-1",
        gmail_thread_id="thread-1",
    )

    class FakeReplyRepository:
        def __init__(self, db: object) -> None:
            pass

        def list_for_draft(self, draft_id: int) -> list[object]:
            assert draft_id == 3
            return [inbound_reply]

    class FakeSentMessageRepository:
        def __init__(self, db: object) -> None:
            pass

        def list_for_draft(self, draft_id: int) -> list[object]:
            assert draft_id == 3
            return [sent_followup]

    monkeypatch.setattr("sales_automation.api.main.EmailReplyRepository", FakeReplyRepository)
    monkeypatch.setattr(
        "sales_automation.api.main.EmailSentMessageRepository",
        FakeSentMessageRepository,
    )
    monkeypatch.setattr(
        "sales_automation.api.main.settings.gmail_sender_email", "sales@example.com"
    )

    conversation = _sent_conversation_for_draft(object(), draft)  # type: ignore[arg-type]

    assert conversation.draft_id == 3
    assert conversation.message_count == 3
    assert conversation.body == "Original approved email"
    assert conversation.original_body == "Original approved email"
    assert conversation.latest_body == "Great, here is my calendar link."
    assert conversation.preview == "Great, here is my calendar link."
    assert conversation.date == followup_at
    assert conversation.timestamp == followup_at
    assert conversation.display_date == followup_at.strftime("%b %-d, %Y, %-I:%M %p")
    assert conversation.status_label == "Sent"
    assert "Original approved email" in conversation.thread_body
    assert "Great, here is my calendar link." in conversation.thread_body
    assert [message.direction for message in conversation.messages] == [
        "outbound",
        "inbound",
        "outbound",
    ]
    assert [message.body for message in conversation.messages] == [
        "Original approved email",
        "Interested",
        "Great, here is my calendar link.",
    ]


def test_respond_to_reply_returns_sent_result(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeReplyClassificationService:
        def __init__(self, db: object) -> None:
            pass

        def send_response_to_reply(self, reply_id: int, *, body: str) -> str:
            calls.append({"reply_id": reply_id, "body": body})
            return "gmail-sent-123"

    monkeypatch.setattr(
        "sales_automation.api.main.ReplyClassificationService",
        FakeReplyClassificationService,
    )

    response = respond_to_reply(
        7,
        body=ReplyResponseRequest(body="Thanks, here is my calendar link."),
        db=object(),  # type: ignore[arg-type]
    )

    assert response.model_dump() == {
        "status": "success",
        "reply_id": 7,
        "sent": True,
        "message_id": "gmail-sent-123",
        "message": "Response sent successfully.",
    }
    assert calls == [{"reply_id": 7, "body": "Thanks, here is my calendar link."}]


def test_respond_to_reply_maps_missing_reply_to_404(monkeypatch) -> None:
    class FakeReplyClassificationService:
        def __init__(self, db: object) -> None:
            pass

        def send_response_to_reply(self, reply_id: int, *, body: str) -> str:
            raise ValueError(f"Email reply not found: {reply_id}")

    monkeypatch.setattr(
        "sales_automation.api.main.ReplyClassificationService",
        FakeReplyClassificationService,
    )

    try:
        respond_to_reply(
            99,
            body=ReplyResponseRequest(body="Checking in."),
            db=object(),  # type: ignore[arg-type]
        )
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Email reply not found: 99"
    else:
        raise AssertionError("Expected HTTPException")


def test_edit_draft_does_not_approve_or_send(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    edited_draft = SimpleNamespace(id=12, status="pending_approval")

    class FakeEmailApprovalService:
        def __init__(self, db: object) -> None:
            pass

        def edit(
            self,
            draft_id: int,
            *,
            subject: str | None = None,
            body: str | None = None,
        ) -> object:
            calls.append({"method": "edit", "draft_id": draft_id, "subject": subject, "body": body})
            return edited_draft

        def approve(self, *args: object, **kwargs: object) -> object:
            calls.append({"method": "approve"})
            raise AssertionError("Edit should not approve the draft")

        def send_approved(self, *args: object, **kwargs: object) -> object:
            calls.append({"method": "send_approved"})
            raise AssertionError("Edit should not send the draft")

    class FakeDb:
        def commit(self) -> None:
            calls.append({"method": "commit"})

    monkeypatch.setattr("sales_automation.api.main.EmailApprovalService", FakeEmailApprovalService)

    response = draft_action(
        12,
        background_tasks=SimpleNamespace(add_task=lambda *args, **kwargs: None),
        body=DraftActionRequest(action="edit", subject="Updated", body="Updated body"),
        db=FakeDb(),  # type: ignore[arg-type]
    )

    assert response == {
        "api_status": "success",
        "message": "Draft edited and saved for approval.",
        "draft_id": 12,
        "status": "pending_approval",
        "sent": False,
        "sheet_update_queued": False,
    }
    assert calls == [
        {"method": "edit", "draft_id": 12, "subject": "Updated", "body": "Updated body"},
        {"method": "commit"},
    ]


def test_classify_replies_enables_slack_notifications_by_default(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeReplyClassificationService:
        def __init__(self, db: object) -> None:
            pass

        def classify_gmail_replies(
            self,
            *,
            query: str,
            limit: int,
            notify_slack: bool,
            max_seconds: float | None = None,
        ) -> ReplyClassificationResult:
            calls.append(
                {
                    "query": query,
                    "limit": limit,
                    "notify_slack": notify_slack,
                    "max_seconds": max_seconds,
                }
            )
            return ReplyClassificationResult(classified=0, skipped=0, failed=0)

    class FakeDb:
        def commit(self) -> None:
            pass

    monkeypatch.setattr(
        "sales_automation.api.main.ReplyClassificationService",
        FakeReplyClassificationService,
    )
    response = classify_replies(
        body=ClassifyRepliesRequest(query="from:lead@example.com is:unread", limit=10),
        db=FakeDb(),  # type: ignore[arg-type]
    )

    assert response.classified == 0
    assert response.status == "success"
    assert response.message == "No replies available for classification."
    assert calls == [
        {
            "query": "from:lead@example.com is:unread",
            "limit": 10,
            "notify_slack": True,
            "max_seconds": 45,
        }
    ]


def test_classify_replies_can_disable_slack_notifications(monkeypatch) -> None:
    calls: list[bool] = []

    class FakeReplyClassificationService:
        def __init__(self, db: object) -> None:
            pass

        def classify_gmail_replies(
            self,
            *,
            query: str,
            limit: int,
            notify_slack: bool,
            max_seconds: float | None = None,
        ) -> ReplyClassificationResult:
            calls.append(notify_slack)
            return ReplyClassificationResult(classified=0, skipped=0, failed=0)

    class FakeDb:
        def commit(self) -> None:
            pass

    monkeypatch.setattr(
        "sales_automation.api.main.ReplyClassificationService",
        FakeReplyClassificationService,
    )
    response = classify_replies(
        body=ClassifyRepliesRequest(
            query="from:lead@example.com is:unread",
            limit=10,
            notify_slack=False,
        ),
        db=FakeDb(),  # type: ignore[arg-type]
    )

    assert response.classified == 0
    assert response.status == "success"
    assert response.message == "No replies available for classification."
    assert calls == [False]


def test_error_response_uses_consistent_shape() -> None:
    response = _error_response(
        status_code=400,
        message="Something went wrong.",
        errors=[{"field": "body.limit", "message": "Invalid value."}],
    )

    assert response.status_code == 400
    assert response.body == (
        b'{"status":"error","message":"Something went wrong.",'
        b'"errors":[{"field":"body.limit","message":"Invalid value."}]}'
    )
