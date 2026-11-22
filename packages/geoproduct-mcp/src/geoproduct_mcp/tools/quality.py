"""Quality: validating a dataset, writing its report, and exporting it."""

from typing import Annotated, Any, Literal

from pydantic import Field, JsonValue

from geoproduct.engine.data import FileSet
from geoproduct.schemas import (
    Area,
    CheckResult,
    Contract,
    Finding,
    QualityMetrics,
    QualityReport,
)
from geoproduct.schemas._base import ShortText, Slug
from geoproduct_mcp.store import DatasetInfo
from geoproduct_mcp.tools.common import (
    Done,
    Job,
    ServerState,
    dataset_result,
    describe,
    ref,
    run_stage,
)

TOP_FINDINGS = 10
REPORT_EXCERPT = 4000


class RuleInput(Contract):
    """A spatial rule every record should satisfy, checked against a reference dataset."""

    name: Slug
    kind: Literal["near", "within", "not_within"]
    reference_dataset_id: ShortText
    max_distance_m: Annotated[float, Field(gt=0, le=50_000)] | None = None
    """For ``near``: the furthest a record may be from the nearest reference feature."""
    categories: list[ShortText] | None = None
    """Only check records in these categories, e.g. ``["highway=bus_stop"]``."""
    reference_categories: list[ShortText] | None = None
    severity: Literal["error", "warning"] = "error"


class ValidationResult(Contract):
    dataset: DatasetInfo
    """The stored quality report; pass its id to generate_quality_report."""
    score: float
    metrics: QualityMetrics
    checks: list[CheckResult]
    findings: list[Finding]
    """The first findings; the dataset holds them all (up to max_findings)."""
    findings_total: int


def validate_dataset(
    state: ServerState,
    job: Job,
    *,
    dataset_id: str,
    checks: list[str] | None = None,
    area: Area | None = None,
    rules: list[RuleInput] | None = None,
    outlier_distance_m: float | None = None,
    duplicate_distance_m: float | None = None,
    name: str | None = None,
) -> Done:
    refs: dict[str, str] = {}
    stage_rules = []
    for index, rule in enumerate(rules or []):
        alias = f"rule-{index}"
        refs[alias] = rule.reference_dataset_id
        body = rule.model_dump(mode="json", exclude_none=True, exclude={"reference_dataset_id"})
        stage_rules.append({**body, "reference": ref(alias)})
    params: dict[str, Any] = {"name": name or dataset_id, "rules": stage_rules}
    if checks is not None:
        params["checks"] = checks
    if area is not None:
        params["area"] = area.model_dump(mode="json", exclude_none=True)
    if outlier_distance_m is not None:
        params["outlier_distance_m"] = outlier_distance_m
    if duplicate_distance_m is not None:
        params["duplicate_distance_m"] = duplicate_distance_m
    info, data, _ = run_stage(
        state,
        job,
        "validate.dataset",
        params=params,
        input_id=dataset_id,
        refs=refs,
        name=name,
        created_by="validate_dataset",
    )
    assert isinstance(data, QualityReport)
    result = ValidationResult(
        dataset=info,
        score=data.score,
        metrics=data.metrics,
        checks=data.checks,
        findings=data.findings[:TOP_FINDINGS],
        findings_total=len(data.findings),
    )
    errors = data.metrics.records_with_errors
    summary = (
        f"Quality score {data.score:.2f} for {data.metrics.record_count} record(s): "
        f"{errors} with errors, {data.metrics.records_with_warnings_only} with warnings only. "
        f"Report stored as {info.dataset_id}."
    )
    return Done(result, summary)


class ReportResult(Contract):
    dataset: DatasetInfo
    markdown: str
    """The beginning of the Markdown report; the full text is in the dataset's files."""
    truncated: bool


def generate_quality_report(
    state: ServerState,
    job: Job,
    *,
    report_dataset_id: str,
    name: str | None = None,
    max_findings: int = 50,
    copy_to: str | None = None,
    overwrite: bool = False,
) -> Done:
    params: dict[str, Any] = {"max_findings": max_findings, "overwrite": overwrite}
    if name:
        params["name"] = name
    if copy_to:
        params["copy_to"] = copy_to
    info, data, _ = run_stage(
        state,
        job,
        "report.quality",
        params=params,
        input_id=report_dataset_id,
        name=name,
        created_by="generate_quality_report",
    )
    assert isinstance(data, FileSet)
    folder = state.workspace.root / info.folder
    markdown = next(
        (
            (folder / f.path).read_text(encoding="utf-8")
            for f in data.files
            if f.path.endswith(".md")
        ),
        "",
    )
    result = ReportResult(
        dataset=info,
        markdown=markdown[:REPORT_EXCERPT],
        truncated=len(markdown) > REPORT_EXCERPT,
    )
    files = ", ".join(f"{info.folder}/{f.path}" for f in data.files)
    summary = f"Wrote the quality report: {files}."
    if data.copies:
        summary += f" Copied to: {', '.join(data.copies)}."
    return Done(result, summary)


def export_geo_dataset(
    state: ServerState,
    job: Job,
    *,
    dataset_id: str,
    format: Literal["geojson", "csv"],
    name: str | None = None,
    copy_to: str | None = None,
    overwrite: bool = False,
    options: dict[str, JsonValue] | None = None,
) -> Done:
    params: dict[str, Any] = {**(options or {}), "overwrite": overwrite}
    if name:
        params["name"] = name
    if copy_to:
        params["copy_to"] = copy_to
    info, data, _ = run_stage(
        state,
        job,
        f"export.{format}",
        params=params,
        input_id=dataset_id,
        name=name,
        created_by="export_geo_dataset",
    )
    assert isinstance(data, FileSet)
    summary = describe(info) + " Files: " + ", ".join(f"{info.folder}/{f.path}" for f in data.files)
    if data.copies:
        summary += f". Copied to: {', '.join(data.copies)}"
    return Done(dataset_result(info, data), summary + ".")
