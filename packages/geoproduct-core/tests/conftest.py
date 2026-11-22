"""Shared test data.

Everything here is invented. Coordinates sit around 0°, 0° in the Gulf of Guinea, the
conventional placeholder location, so no example implies a real place or dataset.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from geoproduct.schemas import Rating, SourceEvaluation

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def evaluation() -> Callable[..., SourceEvaluation]:
    """``evaluation(default=2, licensing=0)`` builds an evaluation with those scores."""

    def build(default: int = 2, **scores: int) -> SourceEvaluation:
        criteria = (
            "authority",
            "accuracy",
            "freshness",
            "coverage",
            "licensing",
            "geographic_precision",
            "verifiability",
        )
        return SourceEvaluation(
            **{
                c: Rating(score=scores.get(c, default), rationale=f"test rating for {c}")
                for c in criteria
            }
        )

    return build


@pytest.fixture
def square() -> dict[str, Any]:
    """A closed square ring about 22 m across."""
    return {
        "type": "Polygon",
        "coordinates": [[[0.0, 0.0], [0.0002, 0.0], [0.0002, 0.0002], [0.0, 0.0002], [0.0, 0.0]]],
    }


@pytest.fixture
def record_data(square: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "rec_example_clinic",
        "name": "Example Community Clinic",
        "category": "healthcare.clinic",
        "address": {"street": "1 Sample Street", "locality": "Exampletown", "country_code": "ZZ"},
        "attributes": {
            "beds": {"value": 12, "basis": "fact", "evidence_ids": ["ev_1"]},
            "height_m": {"value": 9.0, "basis": "estimate", "note": "3 floors x 3 m"},
        },
        "geometry": square,
        "location": {"kind": "building_footprint", "method": "file"},
        "building_match": {
            "status": "exact",
            "building_ref": {"provider": "fixture", "id": "b-1"},
            "method": "contains+name",
            "score": 0.92,
            "reasons": ["point inside footprint", "footprint name matches"],
        },
        "evidence_ids": ["ev_1"],
        "confidence": {"level": "high", "score": 0.9},
        "status": "resolved",
    }
