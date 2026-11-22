from collections.abc import Callable

import pytest
from pydantic import ValidationError

from geoproduct.errors import GeoProductError
from geoproduct.providers import (
    BuildingFootprint,
    BuildingQuery,
    Capability,
    FeatureQuery,
    GeocodeCandidate,
    GeocodeQuery,
    Provider,
    ProviderInfo,
    ProviderResult,
    UsagePolicy,
)
from geoproduct.schemas import Area, ErrorCode

AREA = Area(bbox=(0.0, 0.0, 0.01, 0.01))


class GeocodeOnly(Provider):
    """A provider that answers every query with one fixed, invented address point."""

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="test-geocoder",
            title="Test geocoder",
            capabilities=[Capability.geocode],
            licence="CC0-1.0",
            policy=UsagePolicy(max_requests_per_second=1, bulk_allowed=False),
            credentials=["TEST_GEOCODER_KEY"],
        )

    def geocode(self, query: GeocodeQuery) -> ProviderResult[GeocodeCandidate]:
        candidate = GeocodeCandidate(
            label="1 Sample Street, Exampletown", lon=0.001, lat=0.001, kind="address_point"
        )
        return ProviderResult[GeocodeCandidate](
            items=[candidate], provider=self.info.name, licence=self.info.licence, request_count=1
        )


def test_declared_capabilities() -> None:
    provider = GeocodeOnly()
    assert provider.supports(Capability.geocode)
    assert not provider.supports(Capability.buildings)


def test_implemented_capability() -> None:
    result = GeocodeOnly().geocode(GeocodeQuery(text="1 Sample Street"))
    assert result.items[0].kind == "address_point"
    assert result.provider == "test-geocoder"
    assert not result.truncated


@pytest.mark.parametrize(
    "call",
    [
        lambda p: p.buildings(BuildingQuery(area=AREA)),
        lambda p: p.features(FeatureQuery(area=AREA, tags={"amenity": "clinic"})),
    ],
)
def test_unsupported_capability_is_a_structured_error(call: Callable[[Provider], object]) -> None:
    with pytest.raises(GeoProductError) as exc:
        call(GeocodeOnly())
    assert exc.value.code == ErrorCode.capability_unsupported
    assert exc.value.error.details["provider"] == "test-geocoder"
    assert not exc.value.error.retryable


def test_info_lists_credential_names_not_values() -> None:
    dumped = GeocodeOnly().info.model_dump()
    assert dumped["credentials"] == ["TEST_GEOCODER_KEY"]
    assert dumped["policy"]["max_concurrency"] == 1


def test_result_items_are_validated() -> None:
    with pytest.raises(ValidationError):
        ProviderResult[GeocodeCandidate].model_validate(
            {"items": [{"label": "x", "lon": 200, "lat": 0, "kind": "centroid"}], "provider": "p"}
        )


def test_footprints_must_be_polygonal() -> None:
    with pytest.raises(ValidationError):
        BuildingFootprint.model_validate(
            {
                "ref": {"provider": "p", "id": "1"},
                "geometry": {"type": "Point", "coordinates": [0, 0]},
            }
        )


@pytest.mark.parametrize(
    "data",
    [
        {"text": ""},
        {"text": "x", "limit": 0},
        {"text": "x", "limit": 21},
        {"text": "x", "country_codes": ["ZZZ"]},
        {"text": "x", "language": "english"},
    ],
)
def test_geocode_query_bounds(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GeocodeQuery.model_validate(data)


def test_feature_query_needs_a_tag() -> None:
    with pytest.raises(ValidationError):
        FeatureQuery(area=AREA, tags={})


def test_feature_query_tag_forms() -> None:
    query = FeatureQuery(area=AREA, tags={"amenity": ["clinic", "hospital"], "name": None})
    assert query.tags["name"] is None


def test_usage_policy_bounds() -> None:
    with pytest.raises(ValidationError):
        UsagePolicy(max_requests_per_second=0)
    with pytest.raises(ValidationError):
        UsagePolicy(max_concurrency=0)
