"""The provider interface.

A provider wraps one data service or file type. It declares what it can do, the licence and
attribution of its data, and the usage policy the engine must respect (rate, concurrency,
whether bulk use is allowed). Provider-specific details stay inside the provider: the rest
of the core only sees these models.

Third-party packages add providers through the ``geoproduct.providers`` entry-point group.
"""

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints

from geoproduct.errors import GeoProductError
from geoproduct.schemas._base import Contract, Fraction, ShortText, Slug, Text
from geoproduct.schemas.errors import ErrorCode
from geoproduct.schemas.geometry import Area, BBox, Latitude, Longitude, PolygonalGeometry
from geoproduct.schemas.records import Address, BuildingRef, LocationKind, Record
from geoproduct.schemas.sources import SourceType

ENTRY_POINT_GROUP = "geoproduct.providers"


class Capability(StrEnum):
    geocode = "geocode"
    reverse_geocode = "reverse_geocode"
    features = "features"
    buildings = "buildings"


class UsagePolicy(Contract):
    max_requests_per_second: Annotated[float, Field(gt=0)] | None = None
    max_concurrency: Annotated[int, Field(ge=1)] = 1
    bulk_allowed: bool = True
    max_batch: Annotated[int, Field(ge=1)] | None = None
    """Most items one pipeline run may send to this provider; larger runs are refused."""
    requires_user_agent: bool = False
    terms_url: ShortText | None = None
    notes: Text | None = None


class ProviderInfo(Contract):
    name: Slug
    title: ShortText
    capabilities: list[Capability]
    source_type: SourceType = SourceType.public_api
    """How data from this provider is recorded as a source."""
    licence: ShortText | None = None
    attribution: Text | None = None
    endpoint: ShortText | None = None
    network: bool = False
    """True when calls leave this machine; such calls count towards confirmation limits."""
    policy: UsagePolicy = Field(default_factory=UsagePolicy)
    credentials: list[ShortText] = Field(default_factory=list)
    """Names of the environment variables holding secrets it reads. Never their values."""
    settings: list[ShortText] = Field(default_factory=list)
    """Names of the other environment variables it reads."""
    configured: bool = True
    status_note: Text | None = None
    """Why the provider is not usable yet, when ``configured`` is false."""


CountryCode = Annotated[str, StringConstraints(pattern=r"^[a-z]{2}$")]
LanguageTag = Annotated[str, StringConstraints(pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")]


class GeocodeQuery(Contract):
    text: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    limit: Annotated[int, Field(ge=1, le=20)] = 5
    country_codes: list[CountryCode] = Field(default_factory=list)
    bias: Area | None = None
    """Prefer results here; providers that support it may also restrict to it."""
    language: LanguageTag | None = None


class ReverseGeocodeQuery(Contract):
    lon: Longitude
    lat: Latitude
    language: LanguageTag | None = None


class GeocodeCandidate(Contract):
    label: ShortText
    lon: Longitude
    lat: Latitude
    kind: LocationKind
    precision_m: Annotated[float, Field(ge=0)] | None = None
    score: Fraction | None = None
    """The provider's own relevance score, scaled to 0-1 where it offers one."""
    category: ShortText | None = None
    address: Address | None = None
    bbox: BBox | None = None
    provider_ref: ShortText | None = None


TagValue = str | list[str] | None


class FeatureQuery(Contract):
    area: Area
    tags: Annotated[dict[ShortText, TagValue], Field(min_length=1)]
    """Key to value, list of values, or ``None`` for any value; all keys must match."""
    limit: Annotated[int, Field(ge=1, le=10_000)] = 1_000


class BuildingQuery(Contract):
    area: Area
    limit: Annotated[int, Field(ge=1, le=50_000)] = 10_000


class BuildingFootprint(Contract):
    ref: BuildingRef
    geometry: PolygonalGeometry
    name: ShortText | None = None
    address: Address | None = None
    levels: Annotated[float, Field(ge=0)] | None = None
    height_m: Annotated[float, Field(ge=0)] | None = None
    tags: dict[ShortText, Text] = Field(default_factory=dict)


class ProviderResult[ItemT: BaseModel](Contract):
    items: list[ItemT]
    provider: Slug
    licence: ShortText | None = None
    attribution: Text | None = None
    data_timestamp: ShortText | None = None
    """When the provider's data was last updated, as the provider states it."""
    retrieved_at: AwareDatetime | None = None
    """When this response was fetched; a cached response keeps its original time."""
    from_cache: bool = False
    request_count: Annotated[int, Field(ge=0)] = 0
    truncated: bool = False
    """True when the limit was reached and more results may exist."""


class Provider(ABC):
    """Override the methods for the capabilities listed in :attr:`info`."""

    @property
    @abstractmethod
    def info(self) -> ProviderInfo: ...

    def supports(self, capability: Capability) -> bool:
        return capability in self.info.capabilities

    def geocode(self, query: GeocodeQuery) -> ProviderResult[GeocodeCandidate]:
        raise self.unsupported_error(Capability.geocode)

    def reverse_geocode(self, query: ReverseGeocodeQuery) -> ProviderResult[GeocodeCandidate]:
        raise self.unsupported_error(Capability.reverse_geocode)

    def features(self, query: FeatureQuery) -> ProviderResult[Record]:
        raise self.unsupported_error(Capability.features)

    def buildings(self, query: BuildingQuery) -> ProviderResult[BuildingFootprint]:
        raise self.unsupported_error(Capability.buildings)

    def unsupported_error(self, capability: Capability) -> GeoProductError:
        return GeoProductError(
            ErrorCode.capability_unsupported,
            f"Provider '{self.info.name}' does not support {capability.value}.",
            details={"provider": self.info.name, "capability": capability.value},
            hint="Choose a provider that lists this capability.",
        )
