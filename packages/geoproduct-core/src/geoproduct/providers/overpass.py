"""``overpass``: OpenStreetMap features and building footprints through the Overpass API.

Queries are built from structured tags and an area; no raw Overpass QL is accepted, so a
caller cannot run arbitrary queries. Every query has a server-side timeout and a result
limit, runs one at a time, and is cached in the workspace.

Overpass answers a query that ran out of time or memory with HTTP 200 and a ``remark``.
Such partial answers, and answers without an ``elements`` list, are refused rather than
returned as if they were complete, and are never cached: a busy server must not block the
same query until the cache expires.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

import shapely
from shapely.geometry.base import BaseGeometry

from geoproduct.errors import GeoProductError
from geoproduct.geo.shapes import area_shape
from geoproduct.providers import osm
from geoproduct.providers.base import (
    BuildingFootprint,
    BuildingQuery,
    Capability,
    FeatureQuery,
    Provider,
    ProviderInfo,
    ProviderResult,
    TagValue,
    UsagePolicy,
)
from geoproduct.providers.http import (
    ENV_USER_AGENT,
    ClientFactory,
    HttpResult,
    Throttle,
    same_host,
)
from geoproduct.providers.http import endpoint_from_env as _endpoint
from geoproduct.schemas import Area, ErrorCode, Record, SourceType

ENV_URL: Final = "GEOPRODUCT_OVERPASS_URL"
ENV_TIMEOUT: Final = "GEOPRODUCT_OVERPASS_TIMEOUT_S"
PUBLIC_URL: Final = "https://overpass-api.de/api/interpreter"
DEFAULT_TIMEOUT_S: Final = 90
TERMS_URL: Final = "https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances"
MAX_POLYGON_POINTS: Final = 1000
"""Area polygons with more points are queried by their bounding box and filtered locally."""

_REGEX_SPECIAL = re.compile(r"([.^$*+?()\[\]{}|\\])")


def endpoint_from_env(env: Mapping[str, str]) -> str:
    return _endpoint(env, ENV_URL, PUBLIC_URL)


def timeout_from_env(env: Mapping[str, str]) -> int:
    text = env.get(ENV_TIMEOUT, "").strip()
    if not text:
        return DEFAULT_TIMEOUT_S
    if not text.isdigit() or not 10 <= int(text) <= 900:
        raise GeoProductError(
            ErrorCode.invalid_input, f"{ENV_TIMEOUT} must be a whole number from 10 to 900."
        )
    return int(text)


def quote(text: str) -> str:
    """An Overpass QL string literal."""
    escaped = (
        text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    )
    return f'"{escaped}"'


def tag_filter(key: str, value: TagValue) -> str:
    if value is None:
        return f"[{quote(key)}]"
    if isinstance(value, str):
        return f"[{quote(key)}={quote(value)}]"
    options = "|".join(_REGEX_SPECIAL.sub(r"\\\1", v) for v in value)
    return f"[{quote(key)}~{quote(f'^({options})$')}]"


def area_filters(area: Area) -> list[str]:
    """Overpass area filters whose union covers the area (possibly a little more)."""
    if area.point is not None and area.radius_m is not None:
        lon, lat = area.point
        return [f"(around:{area.radius_m:g},{lat:.7f},{lon:.7f})"]
    if area.bbox is not None:
        west, south, east, north = area.bbox
        boxes = (
            [(west, south, east, north)]
            if west <= east
            else [(west, south, 180.0, north), (-180.0, south, east, north)]
        )
        return [f"({s:.7f},{w:.7f},{n:.7f},{e:.7f})" for w, s, e, n in boxes]
    shape = area_shape(area)
    polygons = list(getattr(shape, "geoms", [shape]))
    filters = []
    for polygon in polygons:
        ring = list(polygon.exterior.coords)
        if len(ring) > MAX_POLYGON_POINTS:
            west, south, east, north = polygon.bounds
            filters.append(f"({south:.7f},{west:.7f},{north:.7f},{east:.7f})")
        else:
            points = " ".join(f"{lat:.7f} {lon:.7f}" for lon, lat in ring[:-1])
            filters.append(f"(poly:{quote(points)})")
    return filters


PARTIAL_REMARKS: Final = ("error", "timed out", "timeout", "out of memory")
"""Words in an Overpass ``remark`` that mean the query stopped before it finished."""


def incomplete(data: Any) -> GeoProductError | None:
    """Why an Overpass answer cannot be used as a complete result, or ``None`` if it can.

    Used as the HTTP client's check, so a refused answer is neither returned nor cached.
    """
    if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
        return GeoProductError(
            ErrorCode.provider_error,
            "Overpass answered without a list of elements.",
            details={"provider": "overpass"},
            hint="Check the endpoint setting; try again later.",
        )
    remark = str(data.get("remark") or "")
    if any(word in remark.lower() for word in PARTIAL_REMARKS):
        timed_out = "timed out" in remark.lower() or "timeout" in remark.lower()
        return GeoProductError(
            ErrorCode.timeout if timed_out else ErrorCode.provider_error,
            "Overpass stopped the query before it finished; the answer would be incomplete.",
            details={"provider": "overpass", "remark": remark[:300]},
            hint="Try again later, or query a smaller area or fewer tags.",
        )
    return None


def build_query(
    selectors: Sequence[str], area: Area, limit: int, timeout_s: int, *, tags: str = ""
) -> str:
    statements = [
        f"{selector}{tags}{where};" for selector in selectors for where in area_filters(area)
    ]
    body = "\n  ".join(statements)
    return f"[out:json][timeout:{timeout_s}];\n(\n  {body}\n);\nout geom {limit + 1};"


class OverpassProvider(Provider):
    def __init__(
        self,
        client_factory: ClientFactory,
        *,
        endpoint: str = PUBLIC_URL,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        user_agent_set: bool = True,
    ) -> None:
        self.endpoint = endpoint
        self.public = same_host(endpoint, PUBLIC_URL)
        self.timeout_s = timeout_s
        configured = user_agent_set or not self.public
        self._info = ProviderInfo(
            name="overpass",
            title="Overpass API (OpenStreetMap features)",
            capabilities=[Capability.features, Capability.buildings],
            source_type=SourceType.osm,
            licence=osm.LICENCE,
            attribution=f"{osm.ATTRIBUTION} ({osm.COPYRIGHT_URL})",
            endpoint=endpoint,
            network=True,
            policy=UsagePolicy(
                max_requests_per_second=1.0,
                max_concurrency=1,
                bulk_allowed=True,
                requires_user_agent=self.public,
                terms_url=TERMS_URL if self.public else None,
                notes=f"One query at a time, each limited to {timeout_s} s on the server. "
                "Results are cached in the workspace.",
            ),
            settings=[ENV_USER_AGENT, ENV_URL, ENV_TIMEOUT],
            configured=configured,
            status_note=None
            if configured
            else f"The public instance requires an identifying {ENV_USER_AGENT}.",
        )
        self.client = client_factory("overpass", Throttle(1.0, 1))

    @property
    def info(self) -> ProviderInfo:
        return self._info

    def features(self, query: FeatureQuery) -> ProviderResult[Record]:
        tags = "".join(tag_filter(k, v) for k, v in query.tags.items())
        text = build_query(["nwr"], query.area, query.limit, self.timeout_s, tags=tags)
        result, elements, timestamp = self._run(text)
        region = area_shape(query.area)
        preferred = list(query.tags)
        records = [
            osm.to_record(element, shape, preferred)
            for element, shape in self._shapes(elements[: query.limit], region)
        ]
        meta = self._meta(result, timestamp, truncated=len(elements) > query.limit)
        return ProviderResult[Record](items=records, **meta)

    def buildings(self, query: BuildingQuery) -> ProviderResult[BuildingFootprint]:
        text = build_query(
            ['way["building"]', 'relation["building"]["type"="multipolygon"]'],
            query.area,
            query.limit,
            self.timeout_s,
        )
        result, elements, timestamp = self._run(text)
        region = area_shape(query.area)
        footprints = [
            footprint
            for element, shape in self._shapes(elements[: query.limit], region)
            if (footprint := osm.to_footprint(element, shape)) is not None
        ]
        meta = self._meta(result, timestamp, truncated=len(elements) > query.limit)
        return ProviderResult[BuildingFootprint](items=footprints, **meta)

    def _run(self, text: str) -> tuple[HttpResult, list[dict[str, Any]], str | None]:
        result = self.client.post_json(
            self.endpoint, {"data": text}, timeout_s=self.timeout_s + 30, check=incomplete
        )
        data = result.data
        elements = [e for e in data["elements"] if isinstance(e, dict)]
        timestamp = (data.get("osm3s") or {}).get("timestamp_osm_base")
        return result, elements, str(timestamp)[:200] if timestamp else None

    @staticmethod
    def _shapes(
        elements: list[dict[str, Any]], region: BaseGeometry
    ) -> list[tuple[dict[str, Any], BaseGeometry]]:
        """Elements with a geometry inside the area, in a stable order."""
        shapely.prepare(region)
        found = [
            (element, shape)
            for element in elements
            if (shape := osm.element_shape(element)) is not None and region.intersects(shape)
        ]
        order = {"node": 0, "way": 1, "relation": 2}
        return sorted(found, key=lambda pair: (order.get(pair[0]["type"], 3), pair[0]["id"]))

    def _meta(
        self, result: HttpResult, timestamp: str | None, *, truncated: bool
    ) -> dict[str, Any]:
        return {
            "provider": "overpass",
            "licence": osm.LICENCE,
            "attribution": self._info.attribution,
            "data_timestamp": timestamp,
            "retrieved_at": result.retrieved_at,
            "from_cache": result.from_cache,
            "request_count": result.requests,
            "truncated": truncated,
        }
