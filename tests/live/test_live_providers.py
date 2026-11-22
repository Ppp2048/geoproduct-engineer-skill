"""Opt-in checks against the public OpenStreetMap services (Nominatim and Overpass).

Every other test runs offline against fixtures and mock services. These send four real
requests, so they run only when asked:

    GEOPRODUCT_LIVE_TESTS=1 GEOPRODUCT_USER_AGENT="my-project/0.1 (you@example.org)" \
        uv run pytest tests/live -m live

The area is a few hundred metres of Greenwich Park, London, chosen because it is mapped
in detail and changes slowly. The assertions check the shape of the answers, which the
code depends on, not their exact content, which mappers may change at any time.
"""

import math
import os
from pathlib import Path

import pytest

from geoproduct.geo.shapes import area_shape, record_shape, to_shape
from geoproduct.providers.base import (
    BuildingQuery,
    FeatureQuery,
    GeocodeQuery,
    ReverseGeocodeQuery,
)
from geoproduct.providers.registry import ProviderRegistry, builtin_providers
from geoproduct.schemas import Area
from geoproduct.workspace import Workspace

if os.environ.get("GEOPRODUCT_LIVE_TESTS") != "1":
    pytest.skip("live service tests; opt in with GEOPRODUCT_LIVE_TESTS=1", allow_module_level=True)
if not os.environ.get("GEOPRODUCT_USER_AGENT"):
    pytest.skip("live service tests need GEOPRODUCT_USER_AGENT", allow_module_level=True)

pytestmark = pytest.mark.live

# Flamsteed House at the Royal Observatory, on the prime meridian.
OBSERVATORY = (-0.0015, 51.4778)
AREA = Area(bbox=(-0.0045, 51.4755, 0.0015, 51.4795))
ODBL = "ODbL-1.0"


def providers(tmp_path: Path) -> ProviderRegistry:
    """A fresh workspace each time, so nothing is answered from an earlier run's cache."""
    return builtin_providers(Workspace(tmp_path), os.environ)


def metres_apart(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[1] + b[1]) / 2)
    dx = (a[0] - b[0]) * 111_320 * math.cos(lat)
    dy = (a[1] - b[1]) * 110_540
    return math.hypot(dx, dy)


def test_nominatim_finds_a_named_place_and_the_cache_answers_a_repeat(tmp_path: Path) -> None:
    nominatim = providers(tmp_path).get("nominatim")
    query = GeocodeQuery(text="Royal Observatory, Greenwich", country_codes=["gb"], limit=3)
    result = nominatim.geocode(query)
    assert result.items, "no candidate returned"
    first = result.items[0]
    assert metres_apart((first.lon, first.lat), OBSERVATORY) < 1_000
    assert first.provider_ref
    assert result.licence == ODBL
    assert result.attribution
    assert not result.from_cache
    assert result.request_count == 1

    again = nominatim.geocode(query)
    assert again.from_cache
    assert again.request_count == 0
    assert again.items == result.items


def test_nominatim_reverse_geocodes_a_point_to_a_nearby_address(tmp_path: Path) -> None:
    lon, lat = OBSERVATORY
    result = (
        providers(tmp_path).get("nominatim").reverse_geocode(ReverseGeocodeQuery(lon=lon, lat=lat))
    )
    assert len(result.items) == 1
    found = result.items[0]
    assert found.label
    assert metres_apart((found.lon, found.lat), OBSERVATORY) < 1_000
    assert found.address is not None
    assert found.address.country_code == "GB"
    assert result.licence == ODBL


def test_overpass_features_stay_in_the_area_and_cite_openstreetmap(tmp_path: Path) -> None:
    query = FeatureQuery(area=AREA, tags={"amenity": None}, limit=50)
    result = providers(tmp_path).get("overpass").features(query)
    assert result.items, "no amenity found in the area"
    area = area_shape(AREA)
    for record in result.items:
        assert record.id.startswith("osm:"), record.id
        shape = record_shape(record)
        assert shape is not None, record.id
        assert shape.intersects(area), record.id
    assert result.licence == ODBL
    assert result.data_timestamp, "Overpass states when its data was last updated"
    assert len(result.items) <= 50


def test_overpass_buildings_are_valid_polygons_with_osm_refs(tmp_path: Path) -> None:
    result = providers(tmp_path).get("overpass").buildings(BuildingQuery(area=AREA, limit=200))
    assert result.items, "no building found in the area"
    search = area_shape(AREA)
    for building in result.items:
        assert building.ref.provider == "osm"
        assert building.ref.id.split("/")[0] in {"way", "relation"}, building.ref.id
        shape = to_shape(building.geometry)
        assert shape.is_valid, building.ref.id
        assert shape.area > 0, building.ref.id
        assert shape.intersects(search), building.ref.id
    assert result.licence == ODBL
