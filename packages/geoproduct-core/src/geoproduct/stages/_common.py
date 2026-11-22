"""Helpers shared by the built-in stages: provenance, reference data and slugs."""

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, StringConstraints
from shapely.geometry.base import BaseGeometry

from geoproduct.engine.data import BuildingSet, RecordSet, StageData
from geoproduct.engine.fingerprint import short_id
from geoproduct.errors import GeoProductError
from geoproduct.geo.shapes import record_shape, to_shape
from geoproduct.providers.base import ProviderInfo, ProviderResult
from geoproduct.schemas import (
    Basis,
    ErrorCode,
    Evidence,
    LicenceNotice,
    ProviderUse,
    Record,
    Source,
)

PathText = Annotated[str, StringConstraints(min_length=1, max_length=1000)]
FileName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")]

SHARE_ALIKE = frozenset({"ODbL-1.0", "CC-BY-SA-2.0", "CC-BY-SA-3.0", "CC-BY-SA-4.0"})


def provider_source(
    info: ProviderInfo, capability: str, query: Any, result: ProviderResult[Any]
) -> Source:
    """The source record for one provider query. Same query and data, same id."""
    query_json = query.model_dump(mode="json") if isinstance(query, BaseModel) else query
    return Source(
        id=short_id("src", info.name, capability, query_json, result.data_timestamp),
        title=f"{info.title}: {capability}",
        type=info.source_type,
        url_or_ref=info.endpoint or f"provider:{info.name}",
        publisher=info.title,
        licence=result.licence or info.licence,
        attribution=result.attribution or info.attribution,
        retrieved_at=result.retrieved_at,
        coverage_note=f"{capability} via provider '{info.name}'",
    )


def provider_use(info: ProviderInfo, results: Iterable[ProviderResult[Any]]) -> ProviderUse:
    results = list(results)
    return ProviderUse(
        provider=info.name,
        endpoint=info.endpoint,
        requests=sum(r.request_count for r in results),
        cache_hits=sum(1 for r in results if r.from_cache),
        data_timestamp=next((r.data_timestamp for r in results if r.data_timestamp), None),
        licence=info.licence,
    )


def stated_fields(record: Record) -> list[str]:
    """The record fields a source states: what its evidence supports."""
    fields = [
        name
        for name, present in (
            ("name", record.name),
            ("category", record.category),
            ("address", record.address),
            ("geometry", record.geometry),
        )
        if present
    ]
    fields += [
        f"attributes.{key}"
        for key, attr in record.attributes.items()
        if attr.basis == Basis.fact and attr.value is not None
    ]
    return fields or ["name"]


def cite(
    record: Record,
    source: Source,
    locator: str,
    *,
    supports: list[str] | None = None,
    note: str | None = None,
) -> tuple[Record, Evidence]:
    """Attach evidence from ``source`` to the record and to its unsupported facts."""
    supports = supports or stated_fields(record)
    evidence = Evidence(
        id=short_id("ev", source.id, record.id, locator),
        source_id=source.id,
        supports=supports,
        locator=locator[:200],
        retrieved_at=source.retrieved_at,
        note=note,
    )
    attributes = {
        key: (
            attr.model_copy(update={"evidence_ids": [*attr.evidence_ids, evidence.id]})
            if f"attributes.{key}" in supports and evidence.id not in attr.evidence_ids
            else attr
        )
        for key, attr in record.attributes.items()
    }
    location = record.location
    if "geometry" in supports and evidence.id not in location.evidence_ids:
        location = location.model_copy(
            update={"evidence_ids": [*location.evidence_ids, evidence.id]}
        )
    evidence_ids = record.evidence_ids
    if evidence.id not in evidence_ids:
        evidence_ids = [*evidence_ids, evidence.id]
    updated = record.model_copy(
        update={"attributes": attributes, "location": location, "evidence_ids": evidence_ids}
    )
    return updated, evidence


def licence_notices(sources: Iterable[Source]) -> list[LicenceNotice]:
    grouped: dict[str, list[Source]] = defaultdict(list)
    for source in sources:
        if source.licence:
            grouped[source.licence].append(source)
    notices = []
    for licence in sorted(grouped):
        members = grouped[licence]
        attributions = sorted({s.attribution for s in members if s.attribution})
        notices.append(
            LicenceNotice(
                licence=licence,
                source_ids=[s.id for s in members],
                attribution="; ".join(attributions)[:2000] if attributions else None,
                share_alike=licence in SHARE_ALIKE,
            )
        )
    return notices


@dataclass(frozen=True)
class RefFeature:
    id: str
    name: str | None
    category: str | None
    shape: BaseGeometry


def reference_features(data: StageData, categories: list[str] | None = None) -> list[RefFeature]:
    """Located features of a records or buildings output, optionally filtered by category."""
    features: list[RefFeature] = []
    if isinstance(data, RecordSet):
        for record in data.records:
            if categories and record.category not in categories:
                continue
            geom = record_shape(record)
            if geom is not None:
                features.append(RefFeature(record.id, record.name, record.category, geom))
    elif isinstance(data, BuildingSet):
        for building in data.buildings:
            category = f"building={building.tags.get('building', 'yes')}"
            if categories and category not in categories:
                continue
            ref_id = f"{building.ref.provider}:{building.ref.id}"
            features.append(
                RefFeature(ref_id, building.name, category, to_shape(building.geometry))
            )
    else:
        raise GeoProductError(
            ErrorCode.invalid_input, "A spatial reference must be records or buildings."
        )
    return features


_UNSAFE = re.compile(r"[^A-Za-z0-9._:/@-]+")


def slug_id(value: str) -> str:
    """Turn a value into something usable as a record id, or '' if nothing is left."""
    cleaned = _UNSAFE.sub("-", value.strip()).strip("-")
    while cleaned and not cleaned[0].isalnum():
        cleaned = cleaned[1:]
    return cleaned[:200]
