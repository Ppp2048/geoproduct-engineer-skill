"""Geography: geocoding and OpenStreetMap features and footprints, through providers.

Geocoding answers come straight back; features and footprints are stored as datasets,
because they can be large, with their provenance (the provider query as a source).
"""

from typing import Any

from pydantic import JsonValue

from geoproduct.engine.data import DataKind, RecordSet
from geoproduct.errors import GeoProductError
from geoproduct.providers.base import (
    Capability,
    GeocodeCandidate,
    GeocodeQuery,
    ProviderResult,
    ReverseGeocodeQuery,
    TagValue,
)
from geoproduct.schemas import Area, ErrorCode
from geoproduct.stages.prepare import geocode_queries
from geoproduct_mcp.tools.common import (
    CONFIRM_ABOVE_CALLS,
    Done,
    Job,
    ServerState,
    dataset_result,
    describe,
    expect_kind,
    run_stage,
)


def _candidates_summary(result: ProviderResult[GeocodeCandidate], what: str) -> str:
    count = len(result.items)
    if not count:
        return f"{result.provider} found nothing for {what}."
    first = result.items[0]
    text = f"{count} candidate(s) from {result.provider} for {what}; first: {first.label}"
    text += f" ({first.kind.value}"
    if first.precision_m is not None:
        text += f", about {first.precision_m:g} m"
    text += ")."
    if count > 1 and all(c.score is None for c in result.items):
        text += (
            " The provider gives no relevance score: compare the candidates with your "
            "evidence instead of taking the first."
        )
    if result.from_cache:
        text += " Served from the cache."
    if result.attribution:
        text += f" Attribution: {result.attribution}"
    return text


def geocode(
    state: ServerState,
    *,
    text: str,
    provider: str,
    limit: int = 5,
    country_codes: list[str] | None = None,
    bias: Area | None = None,
    language: str | None = None,
) -> Done:
    query = GeocodeQuery(
        text=text,
        limit=limit,
        country_codes=country_codes or [],
        bias=bias,
        language=language,
    )
    result = state.providers.require(provider, Capability.geocode).geocode(query)
    return Done(result, _candidates_summary(result, f"'{text[:80]}'"))


def reverse_geocode(
    state: ServerState,
    *,
    lon: float,
    lat: float,
    provider: str,
    language: str | None = None,
) -> Done:
    query = ReverseGeocodeQuery(lon=lon, lat=lat, language=language)
    result = state.providers.require(provider, Capability.reverse_geocode).reverse_geocode(query)
    return Done(result, _candidates_summary(result, f"{lon:.6f}, {lat:.6f}"))


def geocode_records(
    state: ServerState,
    job: Job,
    *,
    dataset_id: str,
    provider: str,
    country_codes: list[str] | None = None,
    bias: Area | None = None,
    use_names: bool = True,
    overwrite: bool = False,
    options: dict[str, JsonValue] | None = None,
    confirm: bool = False,
    name: str | None = None,
) -> Done:
    """Run ``geocode.records`` on a stored dataset: one request per record it looks up.

    More than :data:`CONFIRM_ABOVE_CALLS` requests to a network provider need ``confirm``.
    """
    data = expect_kind(state, dataset_id, {DataKind.records})
    assert isinstance(data, RecordSet)
    lookups = len(geocode_queries(data, use_names=use_names, overwrite=overwrite))
    info = state.providers.require(provider, Capability.geocode).info
    if info.network and lookups > CONFIRM_ABOVE_CALLS and not confirm:
        raise GeoProductError(
            ErrorCode.confirmation_required,
            f"Geocoding this dataset may make {lookups} request(s) to {provider}; it needs "
            "explicit confirmation.",
            details={"requests": lookups, "threshold": CONFIRM_ABOVE_CALLS, "provider": provider},
            hint="Show the user the number of requests. If they agree, call geocode_records "
            "again with confirm=true.",
        )
    params: dict[str, Any] = {
        **(options or {}),
        "provider": provider,
        "country_codes": country_codes or [],
        "use_names": use_names,
        "overwrite": overwrite,
    }
    if bias is not None:
        params["bias"] = bias.model_dump(mode="json", exclude_none=True)
    info_out, result, _ = run_stage(
        state,
        job,
        "geocode.records",
        params=params,
        input_id=dataset_id,
        name=name,
        created_by="geocode_records",
    )
    return Done(dataset_result(info_out, result), describe(info_out))


def search_osm_features(
    state: ServerState,
    job: Job,
    *,
    area: Area,
    tags: dict[str, TagValue],
    provider: str,
    limit: int = 1000,
    allow_truncated: bool = False,
    name: str | None = None,
) -> Done:
    params: dict[str, Any] = {
        "provider": provider,
        "area": area.model_dump(mode="json", exclude_none=True),
        "tags": tags,
        "limit": limit,
        "allow_truncated": allow_truncated,
    }
    info, data, _ = run_stage(
        state,
        job,
        "collect.features",
        params=params,
        name=name,
        created_by="search_osm_features",
    )
    return Done(dataset_result(info, data), describe(info))


def get_building_footprints(
    state: ServerState,
    job: Job,
    *,
    area: Area,
    provider: str,
    limit: int = 10_000,
    allow_truncated: bool = False,
    name: str | None = None,
) -> Done:
    params: dict[str, Any] = {
        "provider": provider,
        "area": area.model_dump(mode="json", exclude_none=True),
        "limit": limit,
        "allow_truncated": allow_truncated,
    }
    info, data, _ = run_stage(
        state,
        job,
        "collect.buildings",
        params=params,
        name=name,
        created_by="get_building_footprints",
    )
    return Done(dataset_result(info, data), describe(info))
