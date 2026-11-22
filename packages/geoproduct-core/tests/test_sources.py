from collections.abc import Callable

import pytest
from pydantic import ValidationError

from geoproduct.schemas import (
    CRITERION_WEIGHTS,
    Criterion,
    Evidence,
    ExcerptCheck,
    Rating,
    Source,
    SourceEvaluation,
    SourceTier,
)

Evaluate = Callable[..., SourceEvaluation]


def test_weights_cover_every_criterion_and_sum_to_one() -> None:
    assert set(CRITERION_WEIGHTS) == set(Criterion)
    assert sum(CRITERION_WEIGHTS.values()) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("default", "overrides", "score", "tier"),
    [
        (3, {}, 1.0, SourceTier.high),
        (2, {}, 0.6667, SourceTier.medium),
        (1, {}, 0.3333, SourceTier.low),
        (3, {"freshness": 0, "coverage": 1}, 0.8333, SourceTier.high),
        (2, {"authority": 3}, 0.7333, SourceTier.medium),
        (2, {"authority": 3, "accuracy": 3}, 0.8, SourceTier.high),
    ],
)
def test_score_and_tier(
    evaluation: Evaluate, default: int, overrides: dict[str, int], score: float, tier: SourceTier
) -> None:
    source = Source(
        id="src_1",
        title="Example open data portal",
        type="open_data_portal",
        url_or_ref="https://data.example.org/facilities",
        evaluation=evaluation(default, **overrides),
    )
    assert source.score == pytest.approx(score, abs=1e-4)
    assert source.tier == tier


def test_licensing_zero_makes_any_source_unusable(evaluation: Evaluate) -> None:
    source = Source(
        id="src_1",
        title="Scraped listing",
        type="other",
        url_or_ref="https://listing.example.com",
        evaluation=evaluation(3, licensing=0),
    )
    assert source.score is not None
    assert source.score > 0.8
    assert source.tier == SourceTier.unusable


def test_score_and_tier_in_input_are_replaced(evaluation: Evaluate) -> None:
    source = Source.model_validate(
        {
            "id": "src_1",
            "title": "Example",
            "type": "document",
            "url_or_ref": "report.pdf",
            "evaluation": evaluation(1).model_dump(),
            "score": 1.0,
            "tier": "high",
        }
    )
    assert source.tier == SourceTier.low
    assert source.score == pytest.approx(0.3333, abs=1e-4)


def test_unevaluated_source_has_no_score() -> None:
    source = Source(id="src_1", title="Example", type="user_file", url_or_ref="a.csv", tier="high")
    assert source.score is None
    assert source.tier is None


@pytest.mark.parametrize("score", [-1, 4])
def test_rating_range(score: int) -> None:
    with pytest.raises(ValidationError):
        Rating(score=score, rationale="out of range")


def test_rating_needs_a_rationale() -> None:
    with pytest.raises(ValidationError):
        Rating(score=2, rationale="")


def test_evidence_must_support_at_least_one_field() -> None:
    with pytest.raises(ValidationError):
        Evidence(id="ev_1", source_id="src_1", supports=[])


def test_evidence_excerpt_starts_unchecked() -> None:
    evidence = Evidence(id="ev_1", source_id="src_1", supports=["name"], excerpt="Example Clinic")
    assert evidence.excerpt_check == ExcerptCheck.not_checked
