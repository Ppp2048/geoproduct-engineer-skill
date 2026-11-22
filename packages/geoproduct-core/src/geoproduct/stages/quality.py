"""Quality stages: per-record confidence, dataset validation and quality gates."""

import statistics
from collections import Counter
from collections.abc import Iterator
from typing import Annotated, Any, Literal, get_args

from pydantic import Field, model_validator
from shapely import STRtree, make_valid
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity

from geoproduct.engine.data import DataKind, RecordSet
from geoproduct.engine.stage import StageContext, StageRef, StageResult, stage
from geoproduct.errors import GeoProductError
from geoproduct.geo.metric import distance_m, haversine_m, metres_to_degrees
from geoproduct.geo.shapes import anchor, area_shape, record_shape, to_shape
from geoproduct.resolve.text import name_key
from geoproduct.schemas import (
    Area,
    Basis,
    CheckResult,
    Confidence,
    ConfidenceLevel,
    Contract,
    ErrorCode,
    Evidence,
    Finding,
    LocationKind,
    MatchStatus,
    QualityMetrics,
    QualityReport,
    Record,
    RecordStatus,
    Severity,
    Source,
)
from geoproduct.schemas._base import Fraction, ShortText, Slug
from geoproduct.stages._common import licence_notices, reference_features

# quality.confidence ------------------------------------------------------------------------

LOCATION_WEIGHT = {
    LocationKind.building_footprint: 1.0,
    LocationKind.entrance: 1.0,
    LocationKind.address_point: 0.85,
    LocationKind.centroid: 0.6,
    LocationKind.approximate: 0.35,
    LocationKind.unknown: 0.25,
}
LOCATING_FIELDS = frozenset({"geometry", "address"})


class ConfidenceParams(Contract):
    high: Fraction = 0.75
    medium: Fraction = 0.5
    low: Fraction = 0.25


@stage(
    name="quality.confidence",
    params=ConfidenceParams,
    input=DataKind.records,
    output=DataKind.records,
    summary="Score each record's confidence from its evidence, source quality, location "
    "precision, building match and consistency, with reasons; set resolved/unresolved status.",
)
def quality_confidence(ctx: StageContext, data: RecordSet, params: ConfidenceParams) -> StageResult:
    evidence = {e.id: e for e in data.evidence}
    sources = {s.id: s for s in data.sources}
    records = []
    for done, record in enumerate(data.records):
        if done % 1000 == 0:
            ctx.check()
        records.append(_confidence(record, evidence, sources, params))
    return StageResult(data.model_copy(update={"records": records}))


def _cited(record: Record) -> list[str]:
    ids = [*record.evidence_ids, *record.location.evidence_ids]
    for attr in record.attributes.values():
        ids += attr.evidence_ids
    return list(dict.fromkeys(ids))


def _confidence(
    record: Record,
    evidence: dict[str, Evidence],
    sources: dict[str, Source],
    params: ConfidenceParams,
) -> Record:
    cited = _cited(record)
    found = [evidence[e] for e in cited if e in evidence]
    missing = [e for e in cited if e not in evidence]
    source_ids = sorted({e.source_id for e in found})
    # Sources that say what the record is. A geocoder or a footprint that only places it
    # counts towards the location score instead.
    describing = {e.source_id for e in found if set(e.supports) - LOCATING_FIELDS}
    describing = describing or set(source_ids)
    reasons: list[str] = []

    if len(describing) >= 2:
        s_evidence = 0.35
        reasons.append(f"{len(describing)} independent sources describe it")
    elif describing:
        s_evidence = 0.2
        reasons.append("one source describes it")
    else:
        s_evidence = 0.0
        reasons.append("no evidence")

    scores = [score for s in source_ids if s in sources and (score := sources[s].score) is not None]
    if scores:
        s_source = 0.2 * max(scores)
        reasons.append(f"best source score {max(scores):.2f}")
    elif source_ids:
        s_source = 0.1
        reasons.append("sources not evaluated")
    else:
        s_source = 0.0

    if record.geometry is None:
        s_location = 0.0
        reasons.append("no location")
    else:
        weight = LOCATION_WEIGHT.get(record.location.kind, 0.25)
        match = record.building_match.status if record.building_match else None
        if match == MatchStatus.exact:
            weight = max(weight, 1.0)
            reasons.append("exact building match")
        elif match == MatchStatus.probable:
            # A footprint taken from this same unconfirmed match proves no more than the match.
            from_match = (record.location.method or "").startswith("match.buildings:")
            weight = 0.85 if from_match else max(weight, 0.85)
            reasons.append("probable building match")
        elif match == MatchStatus.ambiguous:
            reasons.append("building match ambiguous")
        precision = record.location.precision_m
        if precision is not None and precision > 100:
            weight *= 0.5
            reasons.append(f"location precise to only {precision:g} m")
        s_location = 0.3 * weight
        reasons.append(f"location: {record.location.kind.value}")

    unsupported = [
        k for k, a in record.attributes.items() if a.basis == Basis.fact and not a.evidence_ids
    ]
    if record.conflicts:
        s_consistency = 0.0
        reasons.append("sources disagree: " + ", ".join(c.field for c in record.conflicts))
    elif unsupported or missing:
        s_consistency = 0.05
        if unsupported:
            reasons.append("facts without evidence: " + ", ".join(sorted(unsupported)))
        if missing:
            reasons.append(f"{len(missing)} cited evidence id(s) not found")
    else:
        s_consistency = 0.15

    score = round(s_evidence + s_source + s_location + s_consistency, 3)
    status = record.status
    if status not in (RecordStatus.rejected, RecordStatus.conflicting):
        status = RecordStatus.unresolved if record.geometry is None else RecordStatus.resolved
    if status == RecordStatus.unresolved:
        level = ConfidenceLevel.unresolved
    elif score >= params.high:
        level = ConfidenceLevel.high
    elif score >= params.medium:
        level = ConfidenceLevel.medium
    elif score >= params.low:
        level = ConfidenceLevel.low
    else:
        level = ConfidenceLevel.unresolved
    return record.model_copy(
        update={
            "status": status,
            "confidence": Confidence(level=level, score=score, reasons=[r[:2000] for r in reasons]),
        }
    )


# validate.dataset --------------------------------------------------------------------------

Check = Literal[
    "coordinates",
    "crs",
    "geometry",
    "missing_geometry",
    "duplicates",
    "within_area",
    "outliers",
    "evidence",
    "conflicts",
    "rules",
]
CHECKS: tuple[Check, ...] = get_args(Check)
Shapes = dict[str, BaseGeometry | None]


class SpatialRule(Contract):
    """A relationship records should have with a reference layer."""

    name: Slug
    kind: Literal["near", "within", "not_within"]
    reference: StageRef
    max_distance_m: Annotated[float, Field(gt=0, le=50_000)] | None = None
    """For ``near``: the furthest a record may be from the nearest reference feature."""
    categories: list[ShortText] | None = None
    """Which records the rule applies to; all when unset."""
    reference_categories: list[ShortText] | None = None
    severity: Literal["error", "warning"] = "error"

    @model_validator(mode="after")
    def _distance_for_near(self) -> "SpatialRule":
        if (self.kind == "near") != (self.max_distance_m is not None):
            raise ValueError("max_distance_m is required for 'near' rules and only for them")
        return self


class ValidateParams(Contract):
    name: ShortText | None = None
    """The dataset's name in the report; defaults to the stage id."""
    checks: list[Check] = Field(default_factory=lambda: list(CHECKS))
    area: Area | None = None
    """Required by ``within_area``; that check is skipped without it."""
    rules: list[SpatialRule] = Field(default_factory=list)
    outlier_distance_m: Annotated[float, Field(gt=0)] = 5_000.0
    """Records further than this from the dataset's median position are outliers."""
    duplicate_distance_m: Annotated[float, Field(ge=0, le=1_000)] = 25.0
    max_findings: Annotated[int, Field(ge=1, le=100_000)] = 1_000


@stage(
    name="validate.dataset",
    params=ValidateParams,
    input=DataKind.records,
    output=DataKind.report,
    ref_kinds=frozenset({DataKind.records, DataKind.buildings}),
    summary="Check coordinates, CRS, geometry validity, missing geometry, duplicates, the "
    "area, outliers, evidence, conflicts and spatial rules; produce a quality report with a "
    "score.",
)
def validate_dataset(ctx: StageContext, data: RecordSet, params: ValidateParams) -> StageResult:
    findings: list[Finding] = []
    notes: list[str] = []
    shapes = {r.id: record_shape(r) for r in data.records}
    checks: list[str] = list(dict.fromkeys(params.checks))
    if "within_area" in checks and params.area is None:
        checks.remove("within_area")
        notes.append("within_area skipped: no area given")
    if "rules" in checks and not params.rules:
        checks.remove("rules")

    for check in checks:
        ctx.check()
        match check:
            case "coordinates":
                findings += _check_coordinates(data.records)
            case "crs":
                findings += _check_crs(data.records)
            case "geometry":
                findings += _check_geometry(data.records)
            case "missing_geometry":
                findings += [
                    Finding(
                        check="missing_geometry",
                        severity=Severity.warning,
                        message="no location",
                        record_id=r.id,
                    )
                    for r in data.records
                    if r.geometry is None
                ]
            case "duplicates":
                findings += _check_duplicates(data.records, shapes, params.duplicate_distance_m)
            case "within_area":
                assert params.area is not None
                region = area_shape(params.area)
                findings += [
                    Finding(
                        check="within_area",
                        severity=Severity.error,
                        message="outside the requested area",
                        record_id=rid,
                    )
                    for rid, geom in shapes.items()
                    if geom is not None and not geom.intersects(region)
                ]
            case "outliers":
                findings += _check_outliers(shapes, params.outlier_distance_m)
            case "evidence":
                findings += _check_evidence(data)
            case "conflicts":
                findings += [
                    Finding(
                        check="conflicts",
                        severity=Severity.warning,
                        message="sources disagree on " + ", ".join(c.field for c in r.conflicts),
                        record_id=r.id,
                    )
                    for r in data.records
                    if r.conflicts
                ]
            case "rules":
                for rule in params.rules:
                    findings += _check_rule(ctx, data.records, shapes, rule)

    report = _report(
        params.name or ctx.stage_id, data, checks, findings, params.max_findings, notes
    )
    return StageResult(report, input_count=len(data.records))


def _positions(geom_data: Any) -> Iterator[list[float]]:
    if isinstance(geom_data, list) and geom_data and isinstance(geom_data[0], int | float):
        yield geom_data
    elif isinstance(geom_data, list):
        for item in geom_data:
            yield from _positions(item)


def _record_positions(record: Record) -> list[list[float]]:
    if record.geometry is None:
        return []
    data = record.geometry.model_dump(mode="json")
    if data["type"] == "GeometryCollection":
        return [p for g in data["geometries"] for p in _positions(g.get("coordinates"))]
    return list(_positions(data["coordinates"]))


def _check_coordinates(records: list[Record]) -> Iterator[Finding]:
    for record in records:
        positions = _record_positions(record)
        bad = [p for p in positions if not (-180 <= p[0] <= 180 and -90 <= p[1] <= 90)]
        if bad and not all(abs(p[0]) > 1000 or abs(p[1]) > 1000 for p in bad):
            yield Finding(
                check="coordinates",
                severity=Severity.error,
                record_id=record.id,
                message=f"{len(bad)} position(s) outside longitude/latitude range",
                details={"example": bad[0]},
            )
        if any(p[0] == 0 and p[1] == 0 for p in positions):
            yield Finding(
                check="coordinates",
                severity=Severity.warning,
                record_id=record.id,
                message="a position is exactly 0,0, a common placeholder value",
            )


def _check_crs(records: list[Record]) -> Iterator[Finding]:
    for record in records:
        positions = _record_positions(record)
        if positions and all(abs(p[0]) > 1000 or abs(p[1]) > 1000 for p in positions):
            yield Finding(
                check="crs",
                severity=Severity.error,
                record_id=record.id,
                message="coordinates look projected (metres), not WGS 84 degrees",
                details={"example": positions[0]},
            )


def _check_geometry(records: list[Record]) -> Iterator[Finding]:
    for record in records:
        if record.geometry is None:
            continue
        data = record.geometry.model_dump(mode="json")
        problem = _shape_problem(data)
        if problem is None:
            try:
                geom = to_shape(data)
            except Exception as exc:
                problem = f"cannot be read as a geometry ({type(exc).__name__})"
            else:
                if geom.is_empty:
                    problem = "empty geometry"
                elif not geom.is_valid:
                    problem = f"invalid geometry: {explain_validity(geom)}"
                    fixed = make_valid(geom)
                    if not fixed.is_empty:
                        problem += f" (a valid form would be a {fixed.geom_type})"
        if problem:
            yield Finding(
                check="geometry",
                severity=Severity.error,
                record_id=record.id,
                message=problem[:2000],
            )


def _shape_problem(data: dict[str, Any]) -> str | None:
    kind: str = data["type"]
    coords: Any = data.get("coordinates") or []
    if kind == "LineString" and len(coords) < 2:
        return "a line needs at least 2 positions"
    rings: list[Any] = []
    if kind == "Polygon":
        rings = coords
    elif kind == "MultiPolygon":
        rings = [ring for polygon in coords for ring in polygon]
    for ring in rings:
        if len(ring) < 4:
            return "a polygon ring needs at least 4 positions"
        if ring[0] != ring[-1]:
            return "a polygon ring is not closed (first and last positions differ)"
    return None


def _check_duplicates(
    records: list[Record],
    shapes: Shapes,
    distance: float,
) -> Iterator[Finding]:
    seen_geometry: dict[str, str] = {}
    for record in records:
        geom = shapes[record.id]
        if geom is None:
            continue
        if geom.geom_type == "Point":
            x, y = anchor(geom)
            key = f"{x:.6f},{y:.6f}"
        else:
            key = geom.wkb_hex
        if key in seen_geometry:
            yield Finding(
                check="duplicates",
                severity=Severity.warning,
                record_id=record.id,
                message=f"same geometry as {seen_geometry[key]}",
            )
        else:
            seen_geometry[key] = record.id
    located = [(r, g) for r in records if r.name and (g := shapes[r.id]) is not None]
    if not located:
        return
    points = [Point(anchor(g)) for _, g in located]
    tree = STRtree(points)
    for i, (record, _) in enumerate(located):
        reach = metres_to_degrees(distance, points[i].y)
        for j in tree.query(points[i].buffer(reach)):
            other = located[int(j)][0]
            if int(j) <= i or name_key(other.name or "") != name_key(record.name or ""):
                continue
            if distance_m(points[i], points[int(j)]) <= distance:
                yield Finding(
                    check="duplicates",
                    severity=Severity.warning,
                    record_id=other.id,
                    message=f"same name as {record.id} within {distance:g} m",
                )


def _check_outliers(shapes: Shapes, limit_m: float) -> Iterator[Finding]:
    anchors = {rid: anchor(g) for rid, g in shapes.items() if g is not None}
    if len(anchors) < 3:
        return
    lon = statistics.median(a[0] for a in anchors.values())
    lat = statistics.median(a[1] for a in anchors.values())
    for rid, (x, y) in anchors.items():
        d = haversine_m(lon, lat, x, y)
        if d > limit_m:
            yield Finding(
                check="outliers",
                severity=Severity.warning,
                record_id=rid,
                message=f"{d / 1000:.1f} km from the dataset's median position",
                details={"distance_m": round(d)},
            )


def _check_evidence(data: RecordSet) -> Iterator[Finding]:
    evidence = {e.id: e for e in data.evidence}
    sources = {s.id for s in data.sources}
    for e in data.evidence:
        if e.source_id not in sources:
            yield Finding(
                check="evidence",
                severity=Severity.error,
                message=f"evidence {e.id} cites unknown source {e.source_id}",
            )
    for record in data.records:
        cited = _cited(record)
        missing = [e for e in cited if e not in evidence]
        if missing:
            yield Finding(
                check="evidence",
                severity=Severity.error,
                record_id=record.id,
                message=f"cites evidence that does not exist: {', '.join(missing[:5])}",
            )
        if not cited:
            yield Finding(
                check="evidence",
                severity=Severity.warning,
                record_id=record.id,
                message="no evidence for this record",
            )
        for key, attr in record.attributes.items():
            if attr.basis == Basis.fact and attr.value is not None and not attr.evidence_ids:
                yield Finding(
                    check="evidence",
                    severity=Severity.error,
                    record_id=record.id,
                    message=f"attributes.{key} is stated as a fact without evidence",
                )
            if attr.basis == Basis.inference and not attr.note:
                yield Finding(
                    check="evidence",
                    severity=Severity.warning,
                    record_id=record.id,
                    message=f"attributes.{key} is an inference without a note explaining it",
                )


def _check_rule(
    ctx: StageContext,
    records: list[Record],
    shapes: Shapes,
    rule: SpatialRule,
) -> Iterator[Finding]:
    features = reference_features(ctx.load(rule.reference), rule.reference_categories)
    tree = STRtree([f.shape for f in features]) if features else None
    severity = Severity(rule.severity)
    for record in records:
        if rule.categories and record.category not in rule.categories:
            continue
        geom = shapes[record.id]
        if geom is None:
            continue
        if rule.kind == "near":
            assert rule.max_distance_m is not None
            nearest = None
            if tree is not None:
                reach = metres_to_degrees(rule.max_distance_m, anchor(geom)[1])
                distances = [
                    distance_m(geom, features[int(i)].shape) for i in tree.query(geom.buffer(reach))
                ]
                nearest = min(distances) if distances else None
            if nearest is None or nearest > rule.max_distance_m:
                yield Finding(
                    check="rules",
                    severity=severity,
                    record_id=record.id,
                    message=f"rule '{rule.name}': no '{rule.reference.stage}' "
                    f"feature within {rule.max_distance_m:g} m",
                    details={"rule": rule.name},
                )
        else:
            point = Point(anchor(geom))
            inside = tree is not None and any(
                features[int(i)].shape.covers(point) for i in tree.query(point)
            )
            if (rule.kind == "within") != inside:
                where = "outside" if rule.kind == "within" else "inside"
                yield Finding(
                    check="rules",
                    severity=severity,
                    record_id=record.id,
                    message=f"rule '{rule.name}': {where} '{rule.reference.stage}'",
                    details={"rule": rule.name},
                )


def _report(
    dataset: str,
    data: RecordSet,
    checks: list[str],
    findings: list[Finding],
    max_findings: int,
    notes: list[str],
) -> QualityReport:
    records = data.records
    total = len(records)
    errors = {f.record_id for f in findings if f.severity == Severity.error and f.record_id}
    warned = {f.record_id for f in findings if f.severity == Severity.warning and f.record_id}
    warnings_only = warned - errors
    dataset_errors = [f for f in findings if f.severity == Severity.error and not f.record_id]

    facts = [
        a
        for r in records
        for a in r.attributes.values()
        if a.basis == Basis.fact and a.value is not None
    ]
    coverage = sum(1 for a in facts if a.evidence_ids) / len(facts) if facts else None
    clean = total - len(errors) - len(warnings_only)
    score = (clean + 0.75 * len(warnings_only)) / total if total else 1.0
    if dataset_errors:
        score *= 0.5
        notes = [*notes, f"{len(dataset_errors)} dataset-level error(s) halve the score"]

    def share(n: int) -> float:
        return round(n / total, 4) if total else 0.0

    statuses = Counter(r.status.value for r in records)
    checks_out = []
    for check in checks:
        counts = Counter(f.severity for f in findings if f.check == check)
        checks_out.append(
            CheckResult(
                check=check,
                errors=counts[Severity.error],
                warnings=counts[Severity.warning],
                infos=counts[Severity.info],
            )
        )
    order = {Severity.error: 0, Severity.warning: 1, Severity.info: 2}
    ordered = sorted(findings, key=lambda f: (order[f.severity], f.check, f.record_id or ""))
    return QualityReport(
        dataset=dataset,
        checks=checks_out,
        score=round(score, 4),
        metrics=QualityMetrics(
            record_count=total,
            with_geometry=sum(1 for r in records if r.geometry is not None),
            missing_geometry=sum(1 for r in records if r.geometry is None),
            records_with_errors=len(errors),
            records_with_warnings_only=len(warnings_only),
            error_share=share(len(errors)),
            unresolved_share=share(statuses.get(RecordStatus.unresolved.value, 0)),
            conflicting_share=share(statuses.get(RecordStatus.conflicting.value, 0)),
            evidence_coverage=None if coverage is None else round(coverage, 4),
            statuses=dict(sorted(statuses.items())),
            confidence_levels=dict(
                sorted(Counter(r.confidence.level.value for r in records if r.confidence).items())
            ),
            location_kinds=dict(
                sorted(
                    Counter(
                        r.location.kind.value for r in records if r.geometry is not None
                    ).items()
                )
            ),
            match_statuses=dict(
                sorted(
                    Counter(
                        r.building_match.status.value for r in records if r.building_match
                    ).items()
                )
            ),
        ),
        findings=ordered[:max_findings],
        findings_truncated=len(ordered) > max_findings,
        licences=licence_notices(data.sources),
        notes=notes,
    )


# quality.gate ------------------------------------------------------------------------------


class GateParams(Contract):
    min_score: Fraction | None = None
    max_error_share: Fraction | None = None
    max_unresolved_share: Fraction | None = None
    max_conflicting_share: Fraction | None = None
    min_evidence_coverage: Fraction | None = None
    fail_on_checks: list[Check] = Field(default_factory=list)
    """Any error from these checks fails the gate, whatever the shares."""

    @model_validator(mode="after")
    def _has_a_condition(self) -> "GateParams":
        if all(
            value in (None, [])
            for value in (
                self.min_score,
                self.max_error_share,
                self.max_unresolved_share,
                self.max_conflicting_share,
                self.min_evidence_coverage,
                self.fail_on_checks,
            )
        ):
            raise ValueError("a quality gate needs at least one condition")
        return self


@stage(
    name="quality.gate",
    params=GateParams,
    input=DataKind.report,
    output=DataKind.report,
    summary="Stop the run with quality_gate_failed when the report misses any threshold; "
    "nothing after the gate runs. Passes the report on when it meets them all.",
)
def quality_gate(ctx: StageContext, data: QualityReport, params: GateParams) -> StageResult:
    m = data.metrics
    violations: list[str] = []
    if params.min_score is not None and data.score < params.min_score:
        violations.append(f"score {data.score:.2f} is below {params.min_score:.2f}")
    for label, actual, limit in (
        ("error share", m.error_share, params.max_error_share),
        ("unresolved share", m.unresolved_share, params.max_unresolved_share),
        ("conflicting share", m.conflicting_share, params.max_conflicting_share),
    ):
        if limit is not None and actual > limit:
            violations.append(f"{label} {actual:.0%} is above {limit:.0%}")
    if params.min_evidence_coverage is not None and (
        m.evidence_coverage is not None and m.evidence_coverage < params.min_evidence_coverage
    ):
        violations.append(
            f"evidence coverage {m.evidence_coverage:.0%} is below "
            f"{params.min_evidence_coverage:.0%}"
        )
    for check in params.fail_on_checks:
        errors = sum(c.errors for c in data.checks if c.check == check)
        if errors:
            violations.append(f"{errors} error(s) from the '{check}' check")
    if violations:
        raise GeoProductError(
            ErrorCode.quality_gate_failed,
            "Quality gate failed: " + "; ".join(violations) + ".",
            details={"violations": list(violations), "score": data.score, "dataset": data.dataset},
            hint="Read the validation report, fix the data or relax the gate, then resume.",
        )
    summary = "quality gate passed: " + ", ".join(
        f"{k}={v}" for k, v in params.model_dump(exclude_none=True).items() if v not in ([], None)
    )
    return StageResult(
        data.model_copy(update={"notes": [*data.notes, summary[:2000]]}),
        input_count=1,
        notes=[summary],
    )
