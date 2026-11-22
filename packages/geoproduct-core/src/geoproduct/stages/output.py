"""Output stages: GeoJSON and CSV exports and a readable quality report.

Every output is written into the stage's own folder in the run. A copy can also be written
to a folder inside the workspace or an allowed folder; replacing a different existing file
there needs ``overwrite: true``, so an export never silently destroys someone's data.
"""

import csv
import io
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from geoproduct.engine.data import DataKind, FileSet, RecordSet, file_ref
from geoproduct.engine.fingerprint import sha256_bytes, short_id
from geoproduct.engine.stage import StageContext, StageResult, stage
from geoproduct.errors import GeoProductError
from geoproduct.geo.shapes import anchor, record_shape
from geoproduct.schemas import (
    Contract,
    DatasetManifest,
    ErrorCode,
    QualityReport,
    Record,
    Severity,
)
from geoproduct.stages._common import FileName, PathText, licence_notices
from geoproduct.workspace import Workspace


class OutputParams(Contract):
    name: FileName | None = None
    """File name without extension; defaults to the stage id."""
    copy_to: PathText | None = None
    """A folder, inside the workspace or an allowed folder, to copy the output into."""
    overwrite: bool = False
    """Replace files in ``copy_to`` that exist with different content."""


def _stem(ctx: StageContext, params: OutputParams, *suffixes: str) -> str:
    name = params.name or ctx.stage_id
    for suffix in suffixes:
        if name.lower().endswith(suffix):
            return name[: -len(suffix)] or ctx.stage_id
    return name


def _publish(
    ctx: StageContext, params: OutputParams, files: Sequence[tuple[str, bytes, str, int | None]]
) -> FileSet:
    """Write ``(name, content, format, record_count)`` files, and copies if asked."""
    targets: list[tuple[Path, bytes]] = []
    if params.copy_to is not None:
        folder = ctx.workspace.user_path(params.copy_to, must_exist=False)
        if folder.exists() and not folder.is_dir():
            raise GeoProductError(
                ErrorCode.invalid_input,
                "copy_to must be a folder.",
                details={"copy_to": params.copy_to},
            )
        replaced = []
        for name, content, _, _ in files:
            target = folder / name
            if target.exists() and not params.overwrite and not _same_file(target, content):
                replaced.append(str(target))
            targets.append((target, content))
        if replaced:
            raise GeoProductError(
                ErrorCode.confirmation_required,
                f"{len(replaced)} file(s) in copy_to already exist with different content.",
                details={"files": replaced},
                hint="Set overwrite: true to replace them, or choose another folder or name.",
            )

    refs = []
    for name, content, fmt, count in files:
        Workspace.write_bytes_atomic(ctx.output_dir / name, content)
        refs.append(file_ref(ctx.output_dir, name, fmt, count))
    copies = []
    for target, content in targets:
        Workspace.write_bytes_atomic(target, content)
        copies.append(str(target))
    if copies:
        ctx.log("files_copied", files=copies)
    return FileSet(files=refs, copies=copies)


def _same_file(target: Path, content: bytes) -> bool:
    return target.is_file() and sha256_bytes(target.read_bytes()) == sha256_bytes(content)


def _json_bytes(value: Any, *, indent: int | None = None) -> bytes:
    separators = None if indent else (",", ":")
    text = json.dumps(value, ensure_ascii=False, indent=indent, separators=separators)
    return (text + "\n").encode("utf-8")


# export.geojson ----------------------------------------------------------------------------


class GeojsonParams(OutputParams):
    include_provenance: bool = True
    """Add the sources and evidence to the file as foreign members ``sources``/``evidence``."""
    description: str | None = Field(default=None, max_length=2000)


@stage(
    name="export.geojson",
    params=GeojsonParams,
    input=DataKind.records,
    output=DataKind.files,
    retry_safe=False,
    summary="Write the records as an RFC 7946 GeoJSON FeatureCollection with their sources "
    "and evidence, plus a dataset manifest (counts, bounds, licences, file hashes).",
)
def export_geojson(ctx: StageContext, data: RecordSet, params: GeojsonParams) -> StageResult:
    stem = _stem(ctx, params, ".geojson", ".json")
    collection: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": [record.to_feature() for record in data.records],
    }
    if params.include_provenance:
        collection["sources"] = [s.model_dump(mode="json", exclude_none=True) for s in data.sources]
        collection["evidence"] = [
            e.model_dump(mode="json", exclude_none=True) for e in data.evidence
        ]
    content = _json_bytes(collection)
    geojson_name = f"{stem}.geojson"

    manifest = DatasetManifest(
        id=short_id("ds", ctx.run_id, ctx.stage_id, sha256_bytes(content)),
        name=stem,
        description=params.description,
        created_at=ctx.started_at,
        record_count=len(data.records),
        evidence_count=len(data.evidence) if params.include_provenance else 0,
        bbox=_bbox(data.records),
        source_ids=[s.id for s in data.sources],
        run_id=ctx.run_id,
        stage_id=ctx.stage_id,
        licences=licence_notices(data.sources),
        files=[
            {
                "path": geojson_name,
                "sha256": sha256_bytes(content),
                "format": "geojson",
                "size_bytes": len(content),
                "record_count": len(data.records),
            }
        ],
    )
    files = [
        (geojson_name, content, "geojson", len(data.records)),
        (
            f"{stem}.manifest.json",
            _json_bytes(manifest.model_dump(mode="json"), indent=2),
            "json",
            None,
        ),
    ]
    return StageResult(_publish(ctx, params, files), input_count=len(data.records))


def _bbox(records: list[Record]) -> tuple[float, float, float, float] | None:
    bounds = [g.bounds for r in records if (g := record_shape(r)) is not None and not g.is_empty]
    if not bounds:
        return None
    return (
        min(b[0] for b in bounds),
        min(b[1] for b in bounds),
        max(b[2] for b in bounds),
        max(b[3] for b in bounds),
    )


# export.csv --------------------------------------------------------------------------------

FIXED_COLUMNS = (
    "id",
    "name",
    "category",
    "status",
    "confidence",
    "confidence_score",
    "lon",
    "lat",
    "location_kind",
    "precision_m",
    "building_match",
    "building",
    "address",
    "organisation_id",
    "evidence_count",
    "limitations",
)
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
# A leading + or - followed only by digits, spaces and number punctuation (a phone number,
# a negative value) cannot call a function or another program, so it is left unchanged.
_PLAIN_NUMBER = re.compile(r"^[+-][0-9 ().,/-]*$")


class CsvParams(OutputParams):
    attributes: list[str] | Literal["all"] = "all"
    """Attribute columns to include (``attributes.<key>``)."""
    geometry: Literal["point", "wkt", "none"] = "point"
    """``point``: lon/lat of a point on the feature; ``wkt``: also the full geometry."""


@stage(
    name="export.csv",
    params=CsvParams,
    input=DataKind.records,
    output=DataKind.files,
    retry_safe=False,
    summary="Write the records as a flat UTF-8 CSV: one row per record, with location, "
    "confidence, building match and attribute columns. Cells that a spreadsheet would run "
    "as formulas are escaped.",
)
def export_csv(ctx: StageContext, data: RecordSet, params: CsvParams) -> StageResult:
    if params.attributes == "all":
        keys = sorted({k for r in data.records for k in r.attributes})
    else:
        keys = list(dict.fromkeys(params.attributes))
    columns = [c for c in FIXED_COLUMNS if params.geometry != "none" or c not in ("lon", "lat")]
    if params.geometry == "wkt":
        columns.append("wkt")
    columns += [f"attributes.{k}" for k in keys]

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for done, record in enumerate(data.records):
        if done % 1000 == 0:
            ctx.check()
        row = _row(record, keys, params.geometry)
        writer.writerow([_cell(row.get(c)) for c in columns])
    content = ("\ufeff" + buffer.getvalue()).encode("utf-8")  # BOM: spreadsheets detect UTF-8
    stem = _stem(ctx, params, ".csv")
    files = [(f"{stem}.csv", content, "csv", len(data.records))]
    return StageResult(_publish(ctx, params, files), input_count=len(data.records))


def _row(record: Record, keys: list[str], geometry: str) -> dict[str, Any]:
    geom = record_shape(record)
    lon, lat = anchor(geom) if geom is not None else (None, None)
    match = record.building_match
    row: dict[str, Any] = {
        "id": record.id,
        "name": record.name,
        "category": record.category,
        "status": record.status.value,
        "confidence": record.confidence.level.value if record.confidence else None,
        "confidence_score": record.confidence.score if record.confidence else None,
        "lon": None if lon is None else round(lon, 7),
        "lat": None if lat is None else round(lat, 7),
        "location_kind": record.location.kind.value if geom is not None else None,
        "precision_m": record.location.precision_m,
        "building_match": match.status.value if match else None,
        "building": (
            f"{match.building_ref.provider}:{match.building_ref.id}"
            if match and match.building_ref
            else None
        ),
        "address": _address(record),
        "organisation_id": record.organisation_id,
        "evidence_count": len(record.evidence_ids),
        "limitations": "; ".join(record.limitations) or None,
    }
    if geometry == "wkt" and geom is not None:
        row["wkt"] = geom.wkt
    for key in keys:
        attr = record.attributes.get(key)
        row[f"attributes.{key}"] = None if attr is None else attr.value
    return row


def _address(record: Record) -> str | None:
    address = record.address
    if address is None:
        return None
    if address.full:
        return address.full
    street = " ".join(p for p in (address.house_number, address.street) if p)
    parts = [street, address.locality, address.region, address.postcode, address.country_code]
    return ", ".join(p for p in parts if p) or None


def _cell(value: Any) -> str:
    """A CSV cell. Text a spreadsheet would treat as a formula gets a leading apostrophe."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value) if isinstance(value, float) else str(value)
    text = value if isinstance(value, str) else _compact_json(value)
    if text.startswith(_FORMULA_START) and not _PLAIN_NUMBER.match(text):
        return "'" + text
    return text


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# report.quality ----------------------------------------------------------------------------


class ReportParams(OutputParams):
    max_findings: int = Field(default=50, ge=0, le=10_000)
    """How many findings to list in the Markdown report; the JSON has them all."""


@stage(
    name="report.quality",
    params=ReportParams,
    input=DataKind.report,
    output=DataKind.files,
    retry_safe=False,
    summary="Write a quality report as Markdown for people and JSON for tools: score, "
    "metrics, checks, findings, licences and attribution.",
)
def report_quality(ctx: StageContext, data: QualityReport, params: ReportParams) -> StageResult:
    stem = _stem(ctx, params, ".md", ".json")
    files = [
        (f"{stem}.md", _markdown(data, params.max_findings).encode("utf-8"), "markdown", None),
        (f"{stem}.json", _json_bytes(data.model_dump(mode="json"), indent=2), "json", None),
    ]
    return StageResult(_publish(ctx, params, files), input_count=1)


def _md(value: object) -> str:
    """Text safe inside a Markdown table cell."""
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _markdown(report: QualityReport, max_findings: int) -> str:
    m = report.metrics
    lines = [
        f"# Quality report: {_md(report.dataset)}",
        "",
        f"**Score: {report.score:.2f}** (clean records count 1, records with only warnings "
        "count 0.75, records with errors count 0).",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Records | {m.record_count} |",
        f"| With geometry | {m.with_geometry} |",
        f"| Without geometry | {m.missing_geometry} |",
        f"| Records with errors | {m.records_with_errors} ({m.error_share:.0%}) |",
        f"| Records with only warnings | {m.records_with_warnings_only} |",
        f"| Unresolved | {m.unresolved_share:.0%} |",
        f"| Conflicting | {m.conflicting_share:.0%} |",
        "| Evidence coverage of facts | "
        + ("n/a" if m.evidence_coverage is None else f"{m.evidence_coverage:.0%}")
        + " |",
    ]
    for label, counts in (
        ("Statuses", m.statuses),
        ("Confidence", m.confidence_levels),
        ("Location kinds", m.location_kinds),
        ("Building matches", m.match_statuses),
    ):
        if counts:
            summary = ", ".join(f"{k} {v}" for k, v in counts.items())
            lines.append(f"| {label} | {_md(summary)} |")

    lines += [
        "",
        "## Checks",
        "",
        "| Check | Errors | Warnings | Info |",
        "| --- | --- | --- | --- |",
    ]
    lines += [f"| {c.check} | {c.errors} | {c.warnings} | {c.infos} |" for c in report.checks]

    lines += ["", "## Findings", ""]
    if not report.findings:
        lines.append("No findings.")
    else:
        shown = report.findings[:max_findings]
        lines += ["| Severity | Check | Record | Message |", "| --- | --- | --- | --- |"]
        lines += [
            f"| {f.severity.value} | {f.check} | {_md(f.record_id or '-')} | {_md(f.message)} |"
            for f in shown
        ]
        hidden = len(report.findings) - len(shown)
        if hidden or report.findings_truncated:
            more = "more findings are in the JSON report"
            if report.findings_truncated:
                more += "; the validation stage also stopped listing findings at its limit"
            lines += ["", f"{hidden} {more}." if hidden else f"Some {more}."]
    errors = sum(1 for f in report.findings if f.severity == Severity.error)
    if errors:
        lines += ["", f"{errors} error finding(s) listed."]

    lines += ["", "## Licences and attribution", ""]
    if not report.licences:
        lines.append("No licence information was recorded for the sources.")
    for notice in report.licences:
        line = f"- **{_md(notice.licence)}** ({len(notice.source_ids)} source(s))"
        if notice.attribution:
            line += f": {_md(notice.attribution)}"
        if notice.share_alike:
            line += " — share-alike: derived data must keep this licence"
        lines.append(line)

    if report.notes:
        lines += ["", "## Notes", ""]
        lines += [f"- {_md(note)}" for note in report.notes]
    return "\n".join(lines) + "\n"
