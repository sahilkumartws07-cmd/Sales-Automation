import pytest

from sales_automation.services.lead_scoring import _normalize_score_response


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
