"""Spatial stages: building matching and enrichment (nearby, within, measurements)."""

from typing import Annotated, Literal

from pydantic import Field
from shapely import STRtree
from shapely.geometry import Point

from geoproduct.engine.data import BuildingSet, DataKind, RecordSet
from geoproduct.engine.stage import StageContext, StageRef, StageResult, stage
from geoproduct.errors import GeoProductError
from geoproduct.geo.buildings import BuildingIndex, MatchSettings, match_record
from geoproduct.geo.metric import area_m2, distance_m, length_m, metres_to_degrees
from geoproduct.geo.shapes import anchor, record_shape
from geoproduct.schemas import (
    AttributeValue,
    Basis,
    BuildingMatch,
    Contract,
    ErrorCode,
    Evidence,
    Location,
    LocationKind,
    MatchStatus,
    Record,
)
from geoproduct.schemas._base import Fraction, ShortText, Slug
from geoproduct.stages._common import cite, reference_features

# match.buildings ---------------------------------------------------------------------------

MATCH_LIMITATION = "Building match: "
"""Prefix of the limitation ``match.buildings`` writes, so that a rerun replaces it."""


def match_limitation(match: BuildingMatch, *, located: bool = True) -> str | None:
    """One plain sentence on what the building match leaves uncertain, if anything."""
    if match.status == MatchStatus.exact:
        return None
    if not located:
        text = "the record has no location, so it was not matched to a building."
    elif match.status == MatchStatus.probable and match.building_ref is not None:
        ref = f"{match.building_ref.provider}:{match.building_ref.id}"
        text = f"probably {ref}, but no name, address or identifier confirms it."
    elif match.status == MatchStatus.ambiguous:
        refs = [f"{c.ref.provider}:{c.ref.id}" for c in match.candidates[:2]]
        text = f"ambiguous between {' and '.join(refs)}; none was chosen."
    elif match.status == MatchStatus.approximate_point:
        text = "no footprint can be chosen safely; the point stands on its own."
    else:
        text = "no building footprint was found near the point."
    return MATCH_LIMITATION + text


class MatchParams(Contract):
    buildings: StageRef
    """A collect.buildings stage."""
    max_distance_m: Annotated[float, Field(gt=0, le=500)] = 30.0
    min_radius_m: Annotated[float, Field(gt=0, le=100)] = 5.0
    name_threshold: Fraction = 0.85
    ambiguity_margin: Fraction = 0.1
    max_candidates: Annotated[int, Field(ge=2, le=20)] = 5
    assign_geometry: Literal["none", "exact", "exact_or_probable"] = "none"
    """Replace the record's geometry with the matched footprint for these match statuses."""


@stage(
    name="match.buildings",
    params=MatchParams,
    input=DataKind.records,
    output=DataKind.records,
    ref_kinds=frozenset({DataKind.buildings}),
    summary="Match each located record to a building footprint by containment and "
    "proximity, with reasons and candidates. Never attaches a record to the nearest building "
    "without confirming evidence.",
)
def match_buildings(ctx: StageContext, data: RecordSet, params: MatchParams) -> StageResult:
    buildings = ctx.load(params.buildings)
    if not isinstance(buildings, BuildingSet):
        raise GeoProductError(ErrorCode.invalid_input, "'buildings' must be a buildings stage.")
    index = BuildingIndex(buildings.buildings)
    by_ref = {(b.ref.provider, b.ref.id): b for b in buildings.buildings}
    settings = MatchSettings(
        max_distance_m=params.max_distance_m,
        min_radius_m=params.min_radius_m,
        name_threshold=params.name_threshold,
        ambiguity_margin=params.ambiguity_margin,
        max_candidates=params.max_candidates,
    )
    assign = {
        "none": set(),
        "exact": {MatchStatus.exact},
        "exact_or_probable": {MatchStatus.exact, MatchStatus.probable},
    }[params.assign_geometry]
    source = buildings.sources[0] if buildings.sources else None

    records: list[Record] = []
    evidence: list[Evidence] = []
    tally: dict[str, int] = {}
    for done, record in enumerate(data.records):
        if done % 200 == 0:
            ctx.check()
            ctx.progress(done, len(data.records), "matching")
        geom = record_shape(record)
        position = Point(anchor(geom)) if geom is not None else None
        match = match_record(
            record, position, index, settings, precision_m=record.location.precision_m
        )
        tally[match.status.value] = tally.get(match.status.value, 0) + 1
        limitations = [x for x in record.limitations if not x.startswith(MATCH_LIMITATION)]
        if (gap := match_limitation(match, located=position is not None)) is not None:
            limitations.append(gap)
        record = record.model_copy(update={"building_match": match, "limitations": limitations})
        if match.status in assign and match.building_ref is not None and source is not None:
            footprint = by_ref[(match.building_ref.provider, match.building_ref.id)]
            record = record.model_copy(
                update={
                    "geometry": footprint.geometry,
                    "location": Location(
                        kind=LocationKind.building_footprint,
                        method=f"match.buildings:{match.status.value}",
                    ),
                }
            )
            record, ev = cite(
                record,
                source,
                f"{match.building_ref.provider}:{match.building_ref.id}",
                supports=["geometry"],
                note="footprint geometry assigned from the building match",
            )
            evidence.append(ev)
        records.append(record)

    # The footprints' source is credited only when a record names one of its buildings.
    referenced = any(
        r.building_match and (r.building_match.building_ref or r.building_match.candidates)
        for r in records
    )
    output = data.model_copy(update={"records": records}).merged_with(
        buildings.sources if referenced else [], evidence
    )
    notes = [f"{status}: {count}" for status, count in sorted(tally.items())]
    return StageResult(output, notes=notes)


# enrich.nearby -----------------------------------------------------------------------------


class NearbyParams(Contract):
    reference: StageRef
    name: Slug
    """The attribute is called ``nearby_<name>``."""
    radius_m: Annotated[float, Field(gt=0, le=50_000)] = 500.0
    categories: list[ShortText] | None = None
    """Only count reference features with these categories."""
    max_ids: Annotated[int, Field(ge=0, le=50)] = 10


@stage(
    name="enrich.nearby",
    params=NearbyParams,
    input=DataKind.records,
    output=DataKind.records,
    ref_kinds=frozenset({DataKind.records, DataKind.buildings}),
    summary="Count reference features within a radius of each record, with the nearest "
    "distance and ids. The result is marked as an estimate computed from the data.",
)
def enrich_nearby(ctx: StageContext, data: RecordSet, params: NearbyParams) -> StageResult:
    features = reference_features(ctx.load(params.reference), params.categories)
    tree = STRtree([f.shape for f in features]) if features else None
    key = f"nearby_{params.name}"
    note = f"computed: features of '{params.reference.stage}' within {params.radius_m:g} m"
    records = []
    for done, record in enumerate(data.records):
        if done % 200 == 0:
            ctx.check()
        geom = record_shape(record)
        if geom is None:
            records.append(record)
            continue
        hits: list[tuple[float, str]] = []
        if tree is not None:
            reach = metres_to_degrees(params.radius_m, anchor(geom)[1])
            for i in tree.query(geom.buffer(reach)):
                feature = features[int(i)]
                if feature.id == record.id:
                    continue
                d = distance_m(geom, feature.shape)
                if d <= params.radius_m:
                    hits.append((d, feature.id))
        hits.sort()
        value = {
            "count": len(hits),
            "nearest_m": round(hits[0][0], 1) if hits else None,
            "ids": [fid for _, fid in hits[: params.max_ids]],
        }
        records.append(_with_attribute(record, key, value, note))
    return StageResult(data.model_copy(update={"records": records}))


# enrich.within -----------------------------------------------------------------------------


class WithinParams(Contract):
    reference: StageRef
    name: Slug
    """The attribute is called ``within_<name>``."""
    categories: list[ShortText] | None = None
    label: Literal["name", "id"] = "name"


@stage(
    name="enrich.within",
    params=WithinParams,
    input=DataKind.records,
    output=DataKind.records,
    ref_kinds=frozenset({DataKind.records, DataKind.buildings}),
    summary="Record which reference polygons contain each record (a point-in-polygon join).",
)
def enrich_within(ctx: StageContext, data: RecordSet, params: WithinParams) -> StageResult:
    features = [
        f
        for f in reference_features(ctx.load(params.reference), params.categories)
        if f.shape.geom_type in ("Polygon", "MultiPolygon")
    ]
    tree = STRtree([f.shape for f in features]) if features else None
    key = f"within_{params.name}"
    note = f"computed: polygons of '{params.reference.stage}' containing the record"
    records = []
    for done, record in enumerate(data.records):
        if done % 200 == 0:
            ctx.check()
        geom = record_shape(record)
        if geom is None or tree is None:
            records.append(record)
            continue
        point = Point(anchor(geom))
        containing = sorted(
            (features[int(i)] for i in tree.query(point) if features[int(i)].shape.covers(point)),
            key=lambda f: f.id,
        )
        labels = [(f.name or f.id) if params.label == "name" else f.id for f in containing]
        records.append(_with_attribute(record, key, labels, note))
    return StageResult(data.model_copy(update={"records": records}))


# enrich.measure ----------------------------------------------------------------------------


Measure = Literal["area", "length"]


def _all_measures() -> list[Measure]:
    return ["area", "length"]


class MeasureParams(Contract):
    measures: Annotated[list[Measure], Field(min_length=1)] = Field(default_factory=_all_measures)


@stage(
    name="enrich.measure",
    params=MeasureParams,
    input=DataKind.records,
    output=DataKind.records,
    summary="Add area (area_m2) for polygons, and length (length_m) for lines or perimeter "
    "(perimeter_m) for polygons, computed from each record's geometry.",
)
def enrich_measure(ctx: StageContext, data: RecordSet, params: MeasureParams) -> StageResult:
    note = "computed from the geometry in a local projection"
    records = []
    for done, record in enumerate(data.records):
        if done % 500 == 0:
            ctx.check()
        geom = record_shape(record)
        if geom is not None:
            polygonal = geom.geom_type in ("Polygon", "MultiPolygon")
            linear = geom.geom_type in ("LineString", "MultiLineString")
            if "area" in params.measures and polygonal:
                record = _with_attribute(record, "area_m2", round(area_m2(geom), 1), note)
            if "length" in params.measures and (polygonal or linear):
                key = "perimeter_m" if polygonal else "length_m"
                record = _with_attribute(record, key, round(length_m(geom), 1), note)
        records.append(record)
    return StageResult(data.model_copy(update={"records": records}))


def _with_attribute(record: Record, key: str, value: object, note: str) -> Record:
    attributes = dict(record.attributes)
    attributes[key] = AttributeValue.model_validate(
        {"value": value, "basis": Basis.estimate, "note": note}
    )
    return record.model_copy(update={"attributes": attributes})
