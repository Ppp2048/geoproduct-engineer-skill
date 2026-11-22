"""Quality reports: what validation found, the dataset's metrics, and its quality score.

A report is deterministic: the same records and checks give the same report, so it can be
compared between runs. Times live in the run record, not here.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import Field, JsonValue

from geoproduct.schemas._base import (
    SCHEMA_VERSION,
    Contract,
    Fraction,
    Identifier,
    SchemaVersion,
    ShortText,
    Slug,
    Text,
)
from geoproduct.schemas.datasets import LicenceNotice

Count = Annotated[int, Field(ge=0)]


class Severity(StrEnum):
    error = "error"
    """The record is wrong or unsupported as it stands."""
    warning = "warning"
    """The record may be wrong, or is weaker than it could be."""
    info = "info"


class Finding(Contract):
    check: Slug
    severity: Severity
    message: Text
    record_id: Identifier | None = None
    details: dict[str, JsonValue] = Field(default_factory=dict)


class CheckResult(Contract):
    check: Slug
    errors: Count = 0
    warnings: Count = 0
    infos: Count = 0


class QualityMetrics(Contract):
    record_count: Count
    with_geometry: Count
    missing_geometry: Count
    records_with_errors: Count
    records_with_warnings_only: Count
    error_share: Fraction
    """Share of records with at least one error."""
    unresolved_share: Fraction
    conflicting_share: Fraction
    evidence_coverage: Fraction | None = None
    """Share of attributes stated as facts that cite evidence; ``None`` if there are none."""
    statuses: dict[ShortText, Count] = Field(default_factory=dict)
    confidence_levels: dict[ShortText, Count] = Field(default_factory=dict)
    location_kinds: dict[ShortText, Count] = Field(default_factory=dict)
    match_statuses: dict[ShortText, Count] = Field(default_factory=dict)


class QualityReport(Contract):
    schema_version: SchemaVersion = SCHEMA_VERSION
    dataset: ShortText
    """What was checked, usually the id of the stage that produced the records."""
    checks: list[CheckResult]
    score: Fraction
    """``(clean records + 0.75 * records with warnings only) / all records``; 1 when empty."""
    metrics: QualityMetrics
    findings: list[Finding] = Field(default_factory=list)
    findings_truncated: bool = False
    licences: list[LicenceNotice] = Field(default_factory=list)
    notes: list[Text] = Field(default_factory=list)
