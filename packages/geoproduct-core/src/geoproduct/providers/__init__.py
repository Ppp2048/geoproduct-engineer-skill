"""Data providers behind one interface; see :mod:`geoproduct.providers.base`."""

from geoproduct.providers.base import (
    BuildingFootprint,
    BuildingQuery,
    Capability,
    FeatureQuery,
    GeocodeCandidate,
    GeocodeQuery,
    Provider,
    ProviderInfo,
    ProviderResult,
    ReverseGeocodeQuery,
    UsagePolicy,
)

__all__ = [
    "BuildingFootprint",
    "BuildingQuery",
    "Capability",
    "FeatureQuery",
    "GeocodeCandidate",
    "GeocodeQuery",
    "Provider",
    "ProviderInfo",
    "ProviderResult",
    "ReverseGeocodeQuery",
    "UsagePolicy",
]
