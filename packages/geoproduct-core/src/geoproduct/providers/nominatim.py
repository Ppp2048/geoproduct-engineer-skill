"""``nominatim``: geocoding and reverse geocoding with OpenStreetMap data.

The public instance (nominatim.openstreetmap.org) has a strict usage policy: at most one
request per second, an identifying User-Agent, caching, attribution, and no bulk geocoding.
This provider enforces it: requests are throttled and cached, the provider stays unusable
until ``GEOPRODUCT_USER_AGENT`` is set, and a pipeline may geocode at most
:data:`PUBLIC_MAX_BATCH` records per run against the public instance.

Point ``GEOPRODUCT_NOMINATIM_URL`` at a self-hosted instance to lift the batch limit; its
rate can then be raised with ``GEOPRODUCT_NOMINATIM_RATE``.

Nominatim returns no relevance score, so candidates carry ``score=None``. The
``geocode.records`` stage therefore treats several candidates for one query as ambiguous
and leaves the record unresolved, listing them.
"""

import math
from collections.abc import Mapping
from typing import Any, Final

from geoproduct.errors import GeoProductError
from geoproduct.geo.metric import haversine_m
from geoproduct.geo.shapes import area_shape
from geoproduct.providers import osm
from geoproduct.providers.base import (
    Capability,
    GeocodeCandidate,
    GeocodeQuery,
    Provider,
    ProviderInfo,
    ProviderResult,
    ReverseGeocodeQuery,
    UsagePolicy,
)
from geoproduct.providers.http import (
    ENV_USER_AGENT,
    ClientFactory,
    HttpClient,
    HttpResult,
    Throttle,
    same_host,
)
from geoproduct.providers.http import endpoint_from_env as _endpoint
from geoproduct.schemas import Address, ErrorCode, LocationKind, SourceType

ENV_URL: Final = "GEOPRODUCT_NOMINATIM_URL"
ENV_RATE: Final = "GEOPRODUCT_NOMINATIM_RATE"
PUBLIC_URL: Final = "https://nominatim.openstreetmap.org"
PUBLIC_MAX_BATCH: Final = 100
"""Our own cap for one run against the public instance, which does not allow bulk use."""
TERMS_URL: Final = "https://operations.osmfoundation.org/policies/nominatim/"
LOCALITY_KEYS: Final = ("city", "town", "village", "hamlet", "municipality", "suburb")


def endpoint_from_env(env: Mapping[str, str]) -> str:
    return _endpoint(env, ENV_URL, PUBLIC_URL)


def is_public(endpoint: str) -> bool:
    """The OSMF instance, however its URL is written (case, scheme, trailing slash)."""
    return same_host(endpoint, PUBLIC_URL)


def rate_from_env(env: Mapping[str, str], public: bool) -> float:
    text = env.get(ENV_RATE, "").strip()
    if not text or public:
        return 1.0
    try:
        rate = float(text)
    except ValueError:
        rate = 0.0
    if not 0 < rate <= 100:
        raise GeoProductError(
            ErrorCode.invalid_input,
            f"{ENV_RATE} must be a number of requests per second above 0 and at most 100.",
        )
    return rate


class NominatimProvider(Provider):
    def __init__(
        self,
        client_factory: ClientFactory,
        *,
        endpoint: str = PUBLIC_URL,
        rate: float = 1.0,
        user_agent_set: bool = True,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.public = is_public(self.endpoint)
        policy = UsagePolicy(
            max_requests_per_second=rate,
            max_concurrency=1,
            bulk_allowed=not self.public,
            max_batch=PUBLIC_MAX_BATCH if self.public else None,
            requires_user_agent=self.public,
            terms_url=TERMS_URL if self.public else None,
            notes="Results are cached in the workspace; repeated queries are not sent again.",
        )
        configured = user_agent_set or not self.public
        self._info = ProviderInfo(
            name="nominatim",
            title="Nominatim (OpenStreetMap geocoder)",
            capabilities=[Capability.geocode, Capability.reverse_geocode],
            source_type=SourceType.osm,
            licence=osm.LICENCE,
            attribution=f"{osm.ATTRIBUTION} ({osm.COPYRIGHT_URL})",
            endpoint=self.endpoint,
            network=True,
            policy=policy,
            settings=[ENV_USER_AGENT, ENV_URL, ENV_RATE],
            configured=configured,
            status_note=None
            if configured
            else f"The public instance requires an identifying {ENV_USER_AGENT}.",
        )
        self.client: HttpClient = client_factory("nominatim", Throttle(rate, 1))

    @property
    def info(self) -> ProviderInfo:
        return self._info

    def geocode(self, query: GeocodeQuery) -> ProviderResult[GeocodeCandidate]:
        params = {
            "q": query.text,
            "format": "jsonv2",
            "limit": str(query.limit),
            "addressdetails": "1",
        }
        if query.country_codes:
            params["countrycodes"] = ",".join(query.country_codes)
        if query.bias is not None:
            west, south, east, north = area_shape(query.bias).bounds
            params["viewbox"] = f"{west:.7f},{north:.7f},{east:.7f},{south:.7f}"
        if query.language:
            params["accept-language"] = query.language
        result = self.client.get_json(f"{self.endpoint}/search", params)
        items = result.data if isinstance(result.data, list) else []
        candidates = [c for item in items if (c := candidate(item)) is not None]
        return self._result(candidates, result, truncated=len(items) >= query.limit)

    def reverse_geocode(self, query: ReverseGeocodeQuery) -> ProviderResult[GeocodeCandidate]:
        params = {
            "lat": f"{query.lat:.7f}",
            "lon": f"{query.lon:.7f}",
            "format": "jsonv2",
            "addressdetails": "1",
            "zoom": "18",
        }
        if query.language:
            params["accept-language"] = query.language
        result = self.client.get_json(f"{self.endpoint}/reverse", params)
        found = candidate(result.data) if isinstance(result.data, dict) else None
        return self._result([found] if found else [], result, truncated=False)

    def _result(
        self, items: list[GeocodeCandidate], result: HttpResult, *, truncated: bool
    ) -> ProviderResult[GeocodeCandidate]:
        return ProviderResult[GeocodeCandidate](
            items=items,
            provider="nominatim",
            licence=osm.LICENCE,
            attribution=self._info.attribution,
            retrieved_at=result.retrieved_at,
            from_cache=result.from_cache,
            request_count=result.requests,
            truncated=truncated,
        )


def candidate(item: Mapping[str, Any]) -> GeocodeCandidate | None:
    """A candidate from one Nominatim ``jsonv2`` result; ``None`` for an error answer."""
    try:
        lat, lon = float(item["lat"]), float(item["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    details = item.get("address") or {}
    found = _address(details)
    rank = int(item.get("place_rank") or 0)
    if found is not None and found.house_number:
        kind = LocationKind.address_point
    elif rank >= 28:
        kind = LocationKind.centroid
    else:
        kind = LocationKind.approximate
    bbox, precision = _extent(item.get("boundingbox"))
    category = f"{item.get('category')}={item.get('type')}" if item.get("category") else None
    ref = f"{item['osm_type']}/{item['osm_id']}" if item.get("osm_type") else None
    label = str(item.get("display_name") or item.get("name") or f"{lat:.6f}, {lon:.6f}")
    return GeocodeCandidate(
        label=label[:200],
        lon=lon,
        lat=lat,
        kind=kind,
        precision_m=precision,
        score=None,
        category=category[:200] if category else None,
        address=found,
        bbox=bbox,
        provider_ref=ref,
    )


def _address(details: Mapping[str, Any]) -> Address | None:
    country = str(details.get("country_code") or "").upper()
    fields = {
        "house_number": details.get("house_number"),
        "street": details.get("road") or details.get("pedestrian") or details.get("footway"),
        "locality": next((details[k] for k in LOCALITY_KEYS if details.get(k)), None),
        "region": details.get("state") or details.get("province") or details.get("county"),
        "postcode": details.get("postcode"),
        "country_code": country if len(country) == 2 and country.isalpha() else None,
    }
    fields = {k: str(v)[:200] for k, v in fields.items() if v}
    return Address.model_validate(fields) if fields else None


def _extent(box: Any) -> tuple[tuple[float, float, float, float] | None, float | None]:
    """The result's bounding box, and half its diagonal in metres as the precision."""
    try:
        south, north, west, east = (float(v) for v in box)
    except (TypeError, ValueError):
        return None, None
    if not (south <= north and south >= -90 and north <= 90):
        return None, None
    half_diagonal = haversine_m(west, south, east, north) / 2
    precision = max(1.0, round(half_diagonal, 0)) if math.isfinite(half_diagonal) else None
    return (west, south, east, north), precision
