"""Sources and the evidence drawn from them.

The agent finds and judges sources; the server records that judgement and derives a score
from it with fixed, published weights. A score supplied in input is never trusted: it is
always recomputed from the ratings.
"""

from datetime import date
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Final, Literal

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from geoproduct.schemas._base import (
    SCHEMA_VERSION,
    Contract,
    Fraction,
    Identifier,
    SchemaVersion,
    Sha256,
    ShortText,
    Text,
)


class SourceType(StrEnum):
    official_government = "official_government"
    official_organisation = "official_organisation"
    osm = "osm"
    open_data_portal = "open_data_portal"
    public_api = "public_api"
    commercial_api = "commercial_api"
    document = "document"
    user_file = "user_file"
    other = "other"


class Criterion(StrEnum):
    authority = "authority"
    accuracy = "accuracy"
    freshness = "freshness"
    coverage = "coverage"
    licensing = "licensing"
    geographic_precision = "geographic_precision"
    verifiability = "verifiability"


CRITERION_WEIGHTS: Final = MappingProxyType(
    {
        Criterion.authority: 0.20,
        Criterion.accuracy: 0.20,
        Criterion.freshness: 0.10,
        Criterion.coverage: 0.10,
        Criterion.licensing: 0.15,
        Criterion.geographic_precision: 0.15,
        Criterion.verifiability: 0.10,
    }
)
"""Weights for the source score. They sum to 1."""

MAX_RATING: Final = 3


class Rating(Contract):
    """0 = unacceptable or unknown, 1 = weak, 2 = adequate, 3 = strong."""

    score: Annotated[int, Field(ge=0, le=MAX_RATING)]
    rationale: Text


class SourceEvaluation(Contract):
    authority: Rating
    accuracy: Rating
    freshness: Rating
    coverage: Rating
    licensing: Rating
    geographic_precision: Rating
    verifiability: Rating
    evaluated_by: Literal["agent", "user", "rule"] = "agent"

    def rating(self, criterion: Criterion) -> Rating:
        value: Rating = getattr(self, criterion.value)
        return value

    def weighted_score(self) -> float:
        """Weighted mean of the ratings, scaled to 0-1."""
        total = sum(w * self.rating(c).score for c, w in CRITERION_WEIGHTS.items())
        return round(total / MAX_RATING, 4)


class SourceTier(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"
    unusable = "unusable"
    """Licensing rated 0: the source may not be used, whatever its other qualities."""


def tier_for(evaluation: SourceEvaluation) -> SourceTier:
    if evaluation.licensing.score == 0:
        return SourceTier.unusable
    score = evaluation.weighted_score()
    if score >= 0.75:
        return SourceTier.high
    if score >= 0.5:
        return SourceTier.medium
    return SourceTier.low


class Source(Contract):
    schema_version: SchemaVersion = SCHEMA_VERSION
    id: Identifier
    title: ShortText
    type: SourceType
    url_or_ref: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    """A URL, a file name, a dataset identifier or a document citation."""
    publisher: ShortText | None = None
    licence: ShortText | None = None
    """SPDX identifier or licence name. ``None`` means the licence is not known."""
    attribution: Text | None = None
    """The credit line the licence requires, e.g. for OpenStreetMap data."""
    data_as_of: date | None = None
    retrieved_at: AwareDatetime | None = None
    content_sha256: Sha256 | None = None
    coverage_note: Text | None = None
    evaluation: SourceEvaluation | None = None
    score: Fraction | None = None
    """Derived from ``evaluation``; any value given in input is replaced."""
    tier: SourceTier | None = None
    """Derived from ``evaluation``; any value given in input is replaced."""

    @model_validator(mode="after")
    def _derive_score(self) -> "Source":
        if self.evaluation is None:
            self.score, self.tier = None, None
        else:
            self.score, self.tier = self.evaluation.weighted_score(), tier_for(self.evaluation)
        return self


class ExcerptCheck(StrEnum):
    verified = "verified"
    """The excerpt was found in content fetched from the source."""
    not_found = "not_found"
    """Content was fetched, but the excerpt is not in it."""
    not_checked = "not_checked"


class Evidence(Contract):
    """A specific piece of a source that supports specific fields of a record."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    id: Identifier
    source_id: Identifier
    supports: Annotated[list[ShortText], Field(min_length=1)]
    """Record fields this evidence supports, e.g. ``name``, ``geometry``, ``attributes.beds``."""
    excerpt: Text | None = None
    locator: ShortText | None = None
    """Where in the source: a page, section, table row or feature id."""
    excerpt_check: ExcerptCheck = ExcerptCheck.not_checked
    retrieved_at: AwareDatetime | None = None
    last_verified: AwareDatetime | None = None
    note: Text | None = None
