"""Preparation stages: normalising, deduplicating and geocoding records."""

from typing import Annotated, Any, Self

from pydantic import Field, JsonValue, StringConstraints, model_validator

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
from geoproduct.resolve.dedupe import (
    BRAND_KEYS,
    ORGANISATION_KEYS,
    SITE_KEYS,
    WIKIDATA,
    DedupeSettings,
    qid,
    resolve,
)
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


WikidataItem = Annotated[str, StringConstraints(pattern=r"^\s*[Qq][1-9][0-9]{0,11}\s*$")]
"""A Wikidata item id, such as ``Q42``."""


class DedupeParams(Contract):
    name_threshold: Annotated[float, Field(ge=0.5, le=1.0)] = 0.88
    max_distance_m: Annotated[float, Field(gt=0, le=5_000)] = 150.0
    site_keys: list[ShortText] | None = None
    """Identifiers that name one physical site, such as a register or facility code. A
    shared value, with compatible names, nearby positions and nothing contradicting it,
    merges records. Default: none. ``wikidata`` cannot be one: list the Wikidata items that
    name a site in ``wikidata_sites``."""
    organisation_keys: list[ShortText] | None = None
    """Identifiers an organisation shares across its sites. A shared value links records to
    one organisation but never merges them on its own, and a differing value does not keep
    them apart. Default: website, phone, email, brand:wikidata, operator:wikidata."""
    wikidata_sites: list[WikidataItem] | None = None
    """Wikidata items known to name one establishment (checked on the item: an instance of a
    facility, with coordinates at the site). Only these let a shared ``wikidata`` merge."""
    wikidata_organisations: list[WikidataItem] | None = None
    """Wikidata items known to name a company, brand, chain or operator. They link records,
    like an organisation key. Items given as a record's brand or operator count too."""
    id_keys: list[ShortText] | None = None
    """The older single list. In it, website, phone and email count as organisation-level,
    ``wikidata`` is classified item by item, and every other key counts as site-level. It
    fills only a list that is not given."""
    same_category: bool = True
    """Only compare records with the same category (when both have one)."""

    @model_validator(mode="after")
    def _each_key_has_one_level(self) -> Self:
        both = set(self.site_keys or ()) & set(self.organisation_keys or ())
        if both:
            raise ValueError(
                f"{', '.join(sorted(both))} cannot be both a site key and an organisation key"
            )
        classified = set(self.site_keys or ()) & {WIKIDATA, *BRAND_KEYS}
        if classified:
            raise ValueError(
                f"{', '.join(sorted(classified))} cannot be a site key: list the Wikidata "
                "items that name one establishment in wikidata_sites"
            )
        twice = {qid(q) for q in self.wikidata_sites or ()} & {
            qid(q) for q in self.wikidata_organisations or ()
        }
        if twice:
            raise ValueError(
                f"{', '.join(sorted(twice))} cannot be both in wikidata_sites and in "
                "wikidata_organisations"
            )
        return self

    def settings(self) -> DedupeSettings:
        """Resolve the defaults: an explicit list wins, ``id_keys`` comes next, then the
        built-in lists. A key given at one level is dropped from the other's default."""
        site_default, org_default = list(SITE_KEYS), list(ORGANISATION_KEYS)
        if self.id_keys is not None:
            classified = {WIKIDATA, *BRAND_KEYS}
            site_default = [
                k for k in self.id_keys if k not in ORGANISATION_KEYS and k not in classified
            ]
            org_default = [k for k in self.id_keys if k in ORGANISATION_KEYS]
        site = self.site_keys
        org = self.organisation_keys
        if site is None:
            site = [k for k in site_default if k not in (org or ())]
        if org is None:
            org = [k for k in org_default if k not in site]
        return DedupeSettings(
            name_threshold=self.name_threshold,
            max_distance_m=self.max_distance_m,
            site_keys=tuple(dict.fromkeys(site)),
            organisation_keys=tuple(dict.fromkeys(org)),
            wikidata_sites=frozenset(self.wikidata_sites or ()),
            wikidata_organisations=frozenset(self.wikidata_organisations or ()),
            same_category=self.same_category,
        )


@stage(
    name="resolve.deduplicate",
    params=DedupeParams,
    input=DataKind.records,
    output=DataKind.records,
    summary="Merge records that describe one physical site: confirmed by a shared site-level "
    "identifier (such as a register code, or a Wikidata item known to name the site) or a "
    "matching street address, with compatible names, nearby, and not contradicted by "
    "site-level evidence. Disagreeing values are kept side by side as conflicts. "
    "Organisation-level identifiers (website, phone, email, brand), Wikidata items of "
    "unknown meaning and similar names never merge on their own: such records are linked as "
    "one organisation and, when close, marked as possible duplicates for review.",
)
def deduplicate(ctx: StageContext, data: RecordSet, params: DedupeParams) -> StageResult:
    ctx.check()
    settings = params.settings()
    resolution = resolve(data.records, settings)
    notes = [
        f"merged {resolution.merges} duplicate record(s)",
        f"linked {resolution.organisations} multi-site organisation(s)",
        f"kept {resolution.possible} possible duplicate pair(s) apart for review",
    ]
    if resolution.spread:
        notes.append(
            f"compared {resolution.spread} identifier value(s) as organisation-level: records "
            f"at distinct sites (more than {settings.max_distance_m:g} m apart, at different "
            "addresses, or with different site identifiers) carry each"
        )
    if resolution.unclassified:
        notes.append(
            f"merged nothing on {resolution.unclassified} shared Wikidata item(s) of unknown "
            "meaning: list items that name one establishment in wikidata_sites, or "
            "organisations in wikidata_organisations"
        )
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
