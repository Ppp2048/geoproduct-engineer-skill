"""The fixture provider (Sample Town, synthetic) and the provider registry."""

import pytest

from geoproduct.errors import GeoProductError
from geoproduct.providers.base import (
    BuildingQuery,
    Capability,
    FeatureQuery,
    GeocodeQuery,
    Provider,
    ProviderInfo,
    ReverseGeocodeQuery,
)
from geoproduct.providers.fixture import AREA_BBOX, FixtureProvider
from geoproduct.providers.registry import ProviderRegistry, default_providers
from geoproduct.schemas import Area, ErrorCode, LocationKind

TOWN = Area(bbox=AREA_BBOX)
fixture = FixtureProvider()


def test_results_are_deterministic_and_licensed() -> None:
    first = fixture.features(FeatureQuery(area=TOWN, tags={"amenity": None}))
    second = fixture.features(FeatureQuery(area=TOWN, tags={"amenity": None}))
    assert first == second
    assert first.licence == "CC0-1.0"
    assert first.attribution is not None
    assert "Not a real place" in first.attribution
    assert first.request_count == 0


def test_feature_tags_must_all_match() -> None:
    hospitals = fixture.features(FeatureQuery(area=TOWN, tags={"amenity": "hospital"}))
    assert {r.name for r in hospitals.items} == {
        "Sample General Hospital",
        "Sample General Hosp.",
        "Northside Hospital",
    }
    named = fixture.features(
        FeatureQuery(area=TOWN, tags={"amenity": ["hospital", "clinic"], "name": "Hilltop Clinic"})
    )
    assert [r.id for r in named.items] == ["fixture:poi-05"]


def test_buildings_cover_the_town_except_the_park() -> None:
    result = fixture.buildings(BuildingQuery(area=TOWN))
    assert len(result.items) == 60
    names = {b.name for b in result.items if b.name}
    assert {"Sample General Hospital", "Corner Market", "Harbour Point"} <= names


def test_geocoding_an_address_gives_its_building_address_point() -> None:
    result = fixture.geocode(GeocodeQuery(text="9 Third Avenue"))
    top = result.items[0]
    assert top.kind == LocationKind.address_point
    assert top.address is not None
    assert top.address.house_number == "9"
    nothing = fixture.geocode(GeocodeQuery(text="4 Fifth Avenue"))
    assert all(c.kind != LocationKind.address_point for c in nothing.items)


def test_reverse_geocoding_finds_the_nearest_building_within_50_m() -> None:
    near = fixture.reverse_geocode(ReverseGeocodeQuery(lon=0.0043, lat=0.0043))
    assert near.items
    assert near.items[0].address is not None
    far = fixture.reverse_geocode(ReverseGeocodeQuery(lon=0.05, lat=0.05))
    assert far.items == []


def test_registry_requires_a_capable_configured_provider() -> None:
    class GeocodeOnly(Provider):
        @property
        def info(self) -> ProviderInfo:
            return ProviderInfo(
                name="partial",
                title="Partial",
                capabilities=[Capability.geocode],
                credentials=["PARTIAL_API_KEY"],
                configured=False,
            )

    registry = ProviderRegistry([FixtureProvider(), GeocodeOnly()])
    assert registry.names() == ["fixture", "partial"]
    with pytest.raises(GeoProductError) as caught:
        registry.require("partial", Capability.buildings)
    assert caught.value.code == ErrorCode.capability_unsupported
    with pytest.raises(GeoProductError) as caught:
        registry.require("partial", Capability.geocode)
    assert caught.value.code == ErrorCode.auth_missing
    assert caught.value.error.details["credentials"] == ["PARTIAL_API_KEY"]
    assert default_providers().names() == ["fixture"]
