import pytest

from sales_automation.services.lead_scoring import AILeadScoringService, _normalize_score_response


def test_normalize_score_response_accepts_hot_warm_cold() -> None:
    result = _normalize_score_response(
        {
            "score": 9,
            "category": "hot",
            "reason": "Strong fit and urgent buying signals.",
            "factors": {"fit": "high"},
        }
    )

    assert result == {
        "score": 9,
        "category": "HOT",
        "reason": "Strong fit and urgent buying signals.",
        "factors": {"fit": "high"},
    }


def test_normalize_score_response_rejects_out_of_range_score() -> None:
    with pytest.raises(ValueError, match="outside 1-10"):
        _normalize_score_response(
            {
                "score": 11,
                "category": "HOT",
                "reason": "Too high.",
                "factors": {},
            }
        )


def test_normalize_score_response_rejects_unknown_category() -> None:
    with pytest.raises(ValueError, match="invalid category"):
        _normalize_score_response(
            {
                "score": 5,
                "category": "LUKEWARM",
                "reason": "Unknown category.",
                "factors": {},
            }
        )


def test_score_unscored_leads_returns_false_before_workflow_when_no_leads() -> None:
    class FakeScores:
        def list_researched_unscored_leads(self, *, limit: int = 100) -> list[object]:
            assert limit == 25
            return []

    class FakeLogs:
        def record(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("Lead scoring workflow should not start without leads")

    class FakeSession:
        def commit(self) -> None:
            raise AssertionError("No workflow changes should be committed without leads")

    service = AILeadScoringService(session=object())  # type: ignore[arg-type]
    service.scores = FakeScores()  # type: ignore[assignment]
    service.logs = FakeLogs()  # type: ignore[assignment]
    service.session = FakeSession()  # type: ignore[assignment]

    result = service.score_unscored_leads(limit=25)

    assert result.status is False
    assert result.message == "No leads are available for scoring."
    assert result.scored == 0
    assert result.skipped == 0
    assert result.failed == 0
    assert result.errors == []
