"""Preparation stages: normalising, deduplicating and geocoding records."""

from typing import Annotated, Any

from pydantic import Field, JsonValue

from geoproduct.engine.data import DataKind, RecordSet
from geoproduct.engine.stage import Reject, StageContext, StageRef, StageResult, stage
from geoproduct.errors import GeoProductError
from geoproduct.providers.base import (
    Capability,
    CountryCode,
    GeocodeCandidate,
    GeocodeQuery,
    ProviderResult,
)
from geoproduct.resolve.dedupe import DedupeSettings, resolve
from geoproduct.resolve.text import name_key
from geoproduct.schemas import (
    Area,
    Contract,
    ErrorCode,
    Evidence,
    Location,
    Record,
    Source,
)
from geoproduct.schemas._base import Fraction, ShortText, Slug
from geoproduct.stages._common import cite, provider_source, provider_use

# combine.records ---------------------------------------------------------------------------


class CombineParams(Contract):
    add: Annotated[list[StageRef], Field(min_length=1, max_length=20)]
    """Records stages to append to the input, in order."""


@stage(
    name="combine.records",
    params=CombineParams,
    input=DataKind.records,
    output=DataKind.records,
    ref_kinds=frozenset({DataKind.records}),
    summary="Append the records of other stages to the input, with their sources and "
    "evidence. A record id seen before is rejected; use resolve.deduplicate to merge "
    "records that describe the same entity under different ids.",
)
def combine_records(ctx: StageContext, data: RecordSet, params: CombineParams) -> StageResult:
    combined = data
    seen = {r.id for r in data.records}
    rejects: list[Reject] = []
    total = len(data.records)
    for ref in params.add:
        ctx.check()
        other = ctx.load(ref)
        if not isinstance(other, RecordSet):
            raise GeoProductError(
                ErrorCode.invalid_input, f"'{ref.stage}' does not produce records."
            )
        fresh = []
        for record in other.records:
            if record.id in seen:
                rejects.append(
                    Reject(record_id=record.id, reason=f"id already present (from '{ref.stage}')")
                )
            else:
                seen.add(record.id)
                fresh.append(record)
        total += len(other.records)
        combined = combined.model_copy(update={"records": [*combined.records, *fresh]}).merged_with(
            other.sources, other.evidence
        )
    return StageResult(combined, rejects=rejects, input_count=total)


# normalize.records -------------------------------------------------------------------------


class NormalizeParams(Contract):
    categories: dict[ShortText, ShortText] = Field(default_factory=dict)
    """Map source categories to yours, e.g. ``{"amenity=hospital": "health.hospital"}``."""
    aliases: dict[ShortText, list[ShortText]] = Field(default_factory=dict)
    """Extra names, by the record name they belong to."""
    coordinate_decimals: Annotated[int, Field(ge=5, le=9)] = 7
    """7 decimals is about 1 cm, more than any source's real precision."""


@stage(
    name="normalize.records",
    params=NormalizeParams,
    input=DataKind.records,
    output=DataKind.records,
    summary="Tidy whitespace, map categories, add aliases and round coordinates. Names stay "
    "as their sources wrote them; comparison keys are computed where needed.",
)
def normalize_records(ctx: StageContext, data: RecordSet, params: NormalizeParams) -> StageResult:
    categories = {k.casefold(): v for k, v in params.categories.items()}
    aliases = {name_key(k): v for k, v in params.aliases.items()}
    records = []
    for index, record in enumerate(data.records):
        if index % 1000 == 0:
            ctx.check()
        records.append(_normalize(record, categories, aliases, params.coordinate_decimals))
    return StageResult(data.model_copy(update={"records": records}))


def _tidy(text: str | None) -> str | None:
    if text is None:
        return None
    return " ".join(text.split()) or None


def _normalize(
    record: Record,
    categories: dict[str, str],
    aliases: dict[str, list[str]],
    decimals: int,
) -> Record:
    name = _tidy(record.name)
    known = {name_key(name)} if name else set()
    names: list[str] = []
    for alias in [*record.aliases, *(aliases.get(name_key(name), []) if name else [])]:
        tidy = _tidy(alias)
        if tidy and name_key(tidy) not in known:
            known.add(name_key(tidy))
            names.append(tidy)
    category = _tidy(record.category)
    if category:
        category = categories.get(category.casefold(), category)
    address = record.address
    if address is not None:
        address = address.model_copy(
            update={
                field: _tidy(getattr(address, field))
                for field in ("full", "house_number", "street", "locality", "region", "postcode")
            }
        )
    attributes = {
        key: attr.model_copy(update={"value": _tidy(attr.value)})
        if isinstance(attr.value, str)
        else attr
        for key, attr in record.attributes.items()
    }
    geometry = record.geometry
    if geometry is not None:
        rounded = _round(geometry.model_dump(mode="json"), decimals)
        geometry = type(geometry).model_validate(rounded)
    return record.model_copy(
        update={
            "name": name,
            "aliases": names,
            "category": category,
            "address": address,
            "attributes": attributes,
            "geometry": geometry,
            "limitations": list(dict.fromkeys(record.limitations)),
        }
    )


def _round(value: Any, decimals: int) -> Any:
    if isinstance(value, float):
        return round(value, decimals)
    if isinstance(value, list):
        return [_round(v, decimals) for v in value]
    if isinstance(value, dict):
        return {k: _round(v, decimals) for k, v in value.items()}
    return value


# resolve.deduplicate -----------------------------------------------------------------------


class DedupeParams(Contract):
    name_threshold: Annotated[float, Field(ge=0.5, le=1.0)] = 0.88
    max_distance_m: Annotated[float, Field(gt=0, le=5_000)] = 150.0
    id_keys: list[ShortText] = Field(
        default_factory=lambda: ["website", "phone", "email", "wikidata"]
    )
    """Identifiers that mark two records as the same entity (or organisation)."""
    same_category: bool = True
    """Only compare records with the same category (when both have one)."""


@stage(
    name="resolve.deduplicate",
    params=DedupeParams,
    input=DataKind.records,
    output=DataKind.records,
    summary="Merge records that describe the same entity, keeping disagreeing values side by "
    "side as conflicts. Far-apart sites sharing an identifier are linked as one organisation.",
)
def deduplicate(ctx: StageContext, data: RecordSet, params: DedupeParams) -> StageResult:
    ctx.check()
    resolution = resolve(
        data.records,
        DedupeSettings(
            name_threshold=params.name_threshold,
            max_distance_m=params.max_distance_m,
            id_keys=tuple(params.id_keys),
            same_category=params.same_category,
        ),
    )
    notes = [
        f"merged {resolution.merges} duplicate record(s)",
        f"linked {resolution.organisations} multi-site organisation(s)",
    ]
    return StageResult(data.model_copy(update={"records": resolution.records}), notes=notes)


# geocode.records ---------------------------------------------------------------------------


class GeocodeParams(Contract):
    provider: Slug
    country_codes: list[CountryCode] = Field(default_factory=list)
    bias: Area | None = None
    min_score: Fraction = 0.6
    """Candidates the provider scores below this are not used."""
    ambiguity_margin: Fraction = 0.05
    """When the best two candidates score this close, the record is left unresolved."""
    use_names: bool = True
    """Geocode by name when a record has no address."""
    overwrite: bool = False
    """Also geocode records that already have a location."""


def geocode_queries(
    data: RecordSet, *, use_names: bool = True, overwrite: bool = False
) -> list[tuple[int, str]]:
    """The records ``geocode.records`` would look up (by index), with their query text."""
    return [
        (i, text)
        for i, record in enumerate(data.records)
        if (record.geometry is None or overwrite) and (text := _query_text(record, use_names))
    ]


@stage(
    name="geocode.records",
    params=GeocodeParams,
    input=DataKind.records,
    output=DataKind.records,
    summary="Locate records that have an address or name but no geometry. Ambiguous or weak "
    "results leave the record unresolved, with the reason in its limitations.",
)
def geocode_records(ctx: StageContext, data: RecordSet, params: GeocodeParams) -> StageResult:
    provider = ctx.providers.require(params.provider, Capability.geocode)
    todo = geocode_queries(data, use_names=params.use_names, overwrite=params.overwrite)
    policy = provider.info.policy
    if policy.max_batch is not None and len(todo) > policy.max_batch:
        raise GeoProductError(
            ErrorCode.policy_refused,
            f"{len(todo)} records need geocoding, but provider '{provider.info.name}' allows "
            f"at most {policy.max_batch} per run.",
            details={"needed": len(todo), "max_batch": policy.max_batch},
            hint="Split the input, use a self-hosted instance, or choose another provider.",
        )

    records = list(data.records)
    results: list[ProviderResult[GeocodeCandidate]] = []
    source: Source | None = None
    evidence: list[Evidence] = []
    located = 0
    for done, (i, text) in enumerate(todo):
        ctx.check()
        ctx.progress(done, len(todo), "geocoding")
        query = GeocodeQuery(
            text=text, limit=5, country_codes=params.country_codes, bias=params.bias
        )
        result = provider.geocode(query)
        results.append(result)
        if source is None:
            source = provider_source(
                provider.info, "geocode", {"stage": ctx.stage_id, "params": _key(params)}, result
            )
        record, ev = _apply(records[i], text, result.items, params, source, provider.info.name)
        records[i] = record
        if ev is not None:
            evidence.append(ev)
            located += 1

    output = data.model_copy(update={"records": records})
    if source is not None:
        output = output.merged_with([source], evidence)
    return StageResult(
        output,
        providers=[provider_use(provider.info, results)] if results else [],
        notes=[f"located {located} of {len(todo)} record(s) needing a location"],
    )


def _key(params: GeocodeParams) -> JsonValue:
    return params.model_dump(mode="json")


def _query_text(record: Record, use_names: bool) -> str | None:
    address = record.address
    if address is not None:
        if address.full:
            return address.full
        parts = [
            " ".join(p for p in (address.house_number, address.street) if p),
            address.locality,
            address.postcode,
        ]
        text = ", ".join(p for p in parts if p)
        if text:
            return text
    if use_names and record.name:
        return record.name
    return None


def _apply(
    record: Record,
    text: str,
    candidates: list[GeocodeCandidate],
    params: GeocodeParams,
    source: Source,
    provider: str,
) -> tuple[Record, Evidence | None]:
    usable = [c for c in candidates if c.score is None or c.score >= params.min_score]
    if not usable:
        note = f"geocoding '{text}' gave no confident result"
        return _limited(record, note), None
    top = usable[0]
    if len(usable) > 1:
        second = usable[1]
        if (
            top.score is None
            or second.score is None
            or top.score - second.score < params.ambiguity_margin
        ):
            labels = "; ".join(c.label for c in usable[:3])
            return _limited(record, f"geocoding '{text}' was ambiguous: {labels}"), None
    updates: dict[str, Any] = {
        "geometry": {"type": "Point", "coordinates": [top.lon, top.lat]},
        "location": Location(
            kind=top.kind, precision_m=top.precision_m, method=f"geocode:{provider}"
        ),
    }
    supports = ["geometry"]
    if record.address is None and top.address is not None:
        updates["address"] = top.address
        supports.append("address")
    located = Record.model_validate({**record.model_dump(), **_dump(updates)})
    score = f"{top.score:.2f}" if top.score is not None else "unscored"
    return cite(
        located,
        source,
        top.provider_ref or top.label,
        supports=supports,
        note=f"geocoded '{text}' to '{top.label}' ({score})",
    )


def _dump(updates: dict[str, Any]) -> dict[str, Any]:
    return {k: v.model_dump() if hasattr(v, "model_dump") else v for k, v in updates.items()}


def _limited(record: Record, note: str) -> Record:
    if note in record.limitations:
        return record
    return record.model_copy(update={"limitations": [*record.limitations, note[:2000]]})
