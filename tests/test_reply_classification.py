from datetime import UTC, datetime

from sales_automation.models import EmailReply
from sales_automation.services.reply_classification import ReplyClassificationService


class _FailingOpenAI:
    def classify_email_reply(self, **kwargs: object) -> dict[str, object]:
        raise AssertionError("obvious reply should not call AI classifier")


def test_positive_interested_reply_is_classified_as_interested_without_ai() -> None:
    service = ReplyClassificationService.__new__(ReplyClassificationService)
    service.openai_service = _FailingOpenAI()
    reply = EmailReply(
        id=1,
        lead_id=2,
        from_email="buyer@example.com",
        subject="Re: Exploring AI Solutions",
        body="Yes, I would be interested.",
        sentiment=None,
        received_at=datetime.now(UTC),
    )

    result = service._classify_reply(reply)

    assert result["classification"] == "INTERESTED"
    assert result["requires_human_review"] is True


def test_negative_not_interested_reply_still_wins_over_interested_keyword() -> None:
    service = ReplyClassificationService.__new__(ReplyClassificationService)
    service.openai_service = _FailingOpenAI()
    reply = EmailReply(
        id=1,
        lead_id=2,
        from_email="buyer@example.com",
        subject="Re: Exploring AI Solutions",
        body="Thanks, but we are not interested.",
        sentiment=None,
        received_at=datetime.now(UTC),
    )

    result = service._classify_reply(reply)

    assert result["classification"] == "NOT_INTERESTED"
    assert result["requires_human_review"] is False


def test_ambiguous_reply_uses_ai_classification() -> None:
    class FakeOpenAI:
        def classify_email_reply(self, **kwargs: object) -> dict[str, object]:
            assert kwargs["body"] == "Can you send pricing details?"
            return {
                "classification": "INTERESTED",
                "reason": "Asked for pricing details.",
                "requires_human_review": True,
            }

    service = ReplyClassificationService.__new__(ReplyClassificationService)
    service.openai_service = FakeOpenAI()
    reply = EmailReply(
        id=1,
        lead_id=2,
        from_email="buyer@example.com",
        subject="Re: Exploring AI Solutions",
        body="Can you send pricing details?",
        sentiment=None,
        received_at=datetime.now(UTC),
    )

    result = service._classify_reply(reply)

    assert result["classification"] == "INTERESTED"
    assert result["requires_human_review"] is True


def test_ai_not_interested_variant_is_canonicalized() -> None:
    class FakeOpenAI:
        def classify_email_reply(self, **kwargs: object) -> dict[str, object]:
            return {
                "classification": "Not Interested",
                "reason": "No current need.",
                "requires_human_review": False,
            }

    service = ReplyClassificationService.__new__(ReplyClassificationService)
    service.openai_service = FakeOpenAI()
    reply = EmailReply(
        id=1,
        lead_id=2,
        from_email="buyer@example.com",
        subject="Re: Exploring AI Solutions",
        body="We are all set for now.",
        sentiment=None,
        received_at=datetime.now(UTC),
    )

    result = service._classify_reply(reply)

    assert result["classification"] == "NOT_INTERESTED"
    assert result["requires_human_review"] is False
