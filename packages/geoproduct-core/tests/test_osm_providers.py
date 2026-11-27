"""The Nominatim and Overpass providers, against invented responses (no network).

Responses follow the services' documented JSON shapes, with made-up places around 0°, 0°.
"""

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx2
import pytest

from geoproduct.engine import Runner
from geoproduct.engine.data import RecordSet
from geoproduct.engine.spec import validate_spec
from geoproduct.errors import GeoProductError
from geoproduct.providers import osm
from geoproduct.providers.base import (
    BuildingQuery,
    Capability,
    FeatureQuery,
    GeocodeQuery,
    ReverseGeocodeQuery,
)
from geoproduct.providers.http import HttpClient, HttpSettings, Throttle
from geoproduct.providers.overpass import area_filters, build_query, quote, tag_filter
from geoproduct.providers.overpass import incomplete as overpass_incomplete
from geoproduct.providers.registry import ProviderRegistry, builtin_providers
from geoproduct.schemas import Area, ErrorCode, LocationKind, RunStatus
from geoproduct.workspace import Workspace

AGENT = {"GEOPRODUCT_USER_AGENT": "test-suite/1.0 (tests@example.org)"}
BOX = (0.0, 0.0, 0.01, 0.01)


def square(x: float, y: float, d: float) -> list[dict[str, float]]:
    points = [(x, y), (x + d, y), (x + d, y + d), (x, y + d), (x, y)]
    return [{"lon": px, "lat": py} for px, py in points]


NOMINATIM_SEARCH = [
    {
        "place_id": 1,
        "licence": "Data © OpenStreetMap contributors, ODbL 1.0.",
        "osm_type": "node",
        "osm_id": 101,
        "lat": "0.0031",
        "lon": "0.0042",
        "category": "amenity",
        "type": "clinic",
        "place_rank": 30,
        "importance": 0.1,
        "name": "Example Clinic",
        "display_name": "Example Clinic, 7, Sample Road, Exampleton",
        "address": {
            "house_number": "7",
            "road": "Sample Road",
            "town": "Exampleton",
            "postcode": "00000",
            "country_code": "zz",
        },
        "boundingbox": ["0.0030", "0.0032", "0.0041", "0.0043"],
    },
    {
        "osm_type": "way",
        "osm_id": 202,
        "lat": "0.0050",
        "lon": "0.0050",
        "category": "highway",
        "type": "residential",
        "place_rank": 26,
        "display_name": "Sample Road, Exampleton",
        "address": {"road": "Sample Road"},
        "boundingbox": ["0.0040", "0.0060", "0.0040", "0.0060"],
    },
]

OVERPASS_ANSWER = {
    "osm3s": {"timestamp_osm_base": "2026-11-01T00:00:00Z"},
    "elements": [
        {
            "type": "node",
            "id": 1,
            "lat": 0.002,
            "lon": 0.002,
            "tags": {
                "amenity": "clinic",
                "name": "Example Clinic",
                "alt_name": "Old Clinic;Clinic One",
                "healthcare": "clinic",
                "website": "https://clinic.example.org",
                "addr:housenumber": "7",
                "addr:street": "Sample Road",
                "addr:country": "zz",
                "beds": "12",
            },
        },
        {
            "type": "way",
            "id": 2,
            "geometry": square(0.003, 0.003, 0.001),
            "tags": {"building": "hospital", "amenity": "hospital", "name": "Example Hospital"},
        },
        {
            "type": "way",
            "id": 3,
            "geometry": square(0.006, 0.006, 0.001),
            "tags": {"highway": "residential", "name": "Ring Road"},
        },
        {
            "type": "relation",
            "id": 4,
            "tags": {"type": "multipolygon", "amenity": "hospital", "building": "yes"},
            "members": [
                {
                    "type": "way",
                    "ref": 41,
                    "role": "outer",
                    "geometry": square(0.0, 0.0, 0.002)[:3],
                },
                {
                    "type": "way",
                    "ref": 42,
                    "role": "outer",
                    "geometry": square(0.0, 0.0, 0.002)[2:],
                },
                {
                    "type": "way",
                    "ref": 43,
                    "role": "inner",
                    "geometry": square(0.0005, 0.0005, 0.001),
                },
            ],
        },
        {"type": "node", "id": 5, "lat": 0.5, "lon": 0.5, "tags": {"amenity": "clinic"}},
    ],
}


def transport(
    routes: dict[str, Any], seen: list[httpx2.Request] | None = None
) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(request)
        for fragment, body in routes.items():
            if fragment in str(request.url):
                if isinstance(body, httpx2.Response):
                    return body
                return httpx2.Response(200, json=body)
        return httpx2.Response(404)

    return httpx2.MockTransport(handler)


def registry(
    tmp_path: Path, routes: dict[str, Any], env: dict[str, str] | None = None, **kw: Any
) -> ProviderRegistry:
    workspace = Workspace(tmp_path).ensure()
    return builtin_providers(workspace, {**AGENT, **(env or {})}, transport=transport(routes, **kw))


# Query building ----------------------------------------------------------------------------


def test_overpass_strings_and_tag_filters_are_escaped() -> None:
    assert quote('a"b\\c') == '"a\\"b\\\\c"'
    assert tag_filter("amenity", None) == '["amenity"]'
    assert tag_filter("amenity", "clinic") == '["amenity"="clinic"]'
    assert tag_filter("shop", ["a.b", "c|d"]) == '["shop"~"^(a\\\\.b|c\\\\|d)$"]'


def test_overpass_area_filters_cover_every_area_form() -> None:
    assert area_filters(Area(bbox=(0.1, 0.2, 0.3, 0.4))) == [
        "(0.2000000,0.1000000,0.4000000,0.3000000)"
    ]
    across = area_filters(Area(bbox=(179.5, 0.0, -179.5, 1.0)))
    assert len(across) == 2
    assert area_filters(Area(point=(0.001, 0.002), radius_m=250)) == [
        "(around:250,0.0020000,0.0010000)"
    ]
    polygon = Area.model_validate(
        {"polygon": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}
    )
    assert area_filters(polygon) == [
        '(poly:"0.0000000 0.0000000 0.0000000 1.0000000 1.0000000 1.0000000")'
    ]


def test_overpass_queries_have_a_timeout_and_ask_for_one_more_than_the_limit() -> None:
    text = build_query(["nwr"], Area(bbox=BOX), 10, 60, tags='["amenity"]')
    assert text.startswith("[out:json][timeout:60];")
    assert text.endswith("out geom 11;")


# Conversion --------------------------------------------------------------------------------


def test_osm_elements_become_records_with_tags_kept_as_facts() -> None:
    node = OVERPASS_ANSWER["elements"][0]
    shape = osm.element_shape(node)
    assert shape is not None
    record = osm.to_record(node, shape, ["healthcare"])
    assert record.id == "osm:node/1"
    assert record.category == "healthcare=clinic"
    assert record.aliases == ["Old Clinic", "Clinic One"]
    assert record.external_ids == {"osm": "node/1", "website": "https://clinic.example.org"}
    assert record.address is not None
    assert record.address.country_code == "ZZ"
    assert record.attributes["beds"].value == "12"
    assert "addr:street" not in record.attributes
    assert record.location.kind == LocationKind.address_point
    assert record.location.precision_m is None


def test_closed_ways_are_areas_only_when_their_tags_say_so() -> None:
    hospital, ring = OVERPASS_ANSWER["elements"][1], OVERPASS_ANSWER["elements"][2]
    assert osm.element_shape(hospital).geom_type == "Polygon"  # type: ignore[union-attr]
    assert osm.element_shape(ring).geom_type == "LineString"  # type: ignore[union-attr]


def test_multipolygon_relations_are_assembled_with_their_holes() -> None:
    relation = OVERPASS_ANSWER["elements"][3]
    shape = osm.element_shape(relation)
    assert shape is not None
    assert shape.geom_type == "Polygon"
    assert shape.area == pytest.approx(0.002**2 - 0.001**2)


def test_heights_and_levels_are_read_only_when_plainly_stated() -> None:
    assert osm.number("12") == 12.0
    assert osm.number("12.5 m") == 12.5
    assert osm.number("approx 12") is None
    assert osm.number("40'") is None
    assert osm.number(None) is None


# Nominatim ---------------------------------------------------------------------------------


def test_nominatim_candidates_carry_kind_precision_and_no_invented_score(
    tmp_path: Path,
) -> None:
    seen: list[httpx2.Request] = []
    providers = registry(tmp_path, {"/search": NOMINATIM_SEARCH}, seen=seen)
    query = GeocodeQuery(text="7 Sample Road", limit=2, country_codes=["zz"])
    result = providers.get("nominatim").geocode(query)
    first, second = result.items
    assert (first.lon, first.lat) == (0.0042, 0.0031)
    assert first.kind == LocationKind.address_point
    assert first.score is None
    assert first.provider_ref == "node/101"
    assert first.address is not None
    assert first.address.locality == "Exampleton"
    assert first.precision_m is not None
    assert first.precision_m < 20
    assert second.kind == LocationKind.approximate
    assert result.licence == "ODbL-1.0"
    assert result.truncated
    params = parse_qs(seen[0].url.query.decode())
    assert params["countrycodes"] == ["zz"]
    assert params["format"] == ["jsonv2"]


def test_nominatim_reverse_with_no_result_is_empty(tmp_path: Path) -> None:
    providers = registry(tmp_path, {"/reverse": {"error": "Unable to geocode"}})
    result = providers.get("nominatim").reverse_geocode(ReverseGeocodeQuery(lon=0, lat=0))
    assert result.items == []


def test_public_endpoints_need_a_user_agent(tmp_path: Path) -> None:
    providers = builtin_providers(Workspace(tmp_path), {})
    for name in ("nominatim", "overpass"):
        info = providers.get(name).info
        assert not info.configured
        assert info.network
        assert "GEOPRODUCT_USER_AGENT" in info.settings
    with pytest.raises(GeoProductError) as caught:
        providers.require("nominatim", Capability.geocode)
    assert caught.value.code == ErrorCode.auth_missing


def test_self_hosted_nominatim_lifts_the_bulk_limit(tmp_path: Path) -> None:
    env = {
        "GEOPRODUCT_NOMINATIM_URL": "http://geocoder.internal:8080/",
        "GEOPRODUCT_NOMINATIM_RATE": "20",
    }
    providers = builtin_providers(Workspace(tmp_path), env)
    info = providers.get("nominatim").info
    assert info.configured
    assert info.endpoint == "http://geocoder.internal:8080"
    assert info.policy.max_batch is None
    assert info.policy.max_requests_per_second == 20
    public = builtin_providers(Workspace(tmp_path), AGENT).get("nominatim").info.policy
    assert public.max_batch == 100
    assert not public.bulk_allowed


@pytest.mark.parametrize(
    "env",
    [
        {"GEOPRODUCT_NOMINATIM_URL": "http://x.example", "GEOPRODUCT_NOMINATIM_RATE": "0"},
        {"GEOPRODUCT_OVERPASS_TIMEOUT_S": "5"},
    ],
)
def test_invalid_provider_settings_are_refused(tmp_path: Path, env: dict[str, str]) -> None:
    with pytest.raises(GeoProductError):
        builtin_providers(Workspace(tmp_path), env)


# Overpass ----------------------------------------------------------------------------------


def test_overpass_features_are_filtered_to_the_area_and_ordered(tmp_path: Path) -> None:
    seen: list[httpx2.Request] = []
    providers = registry(tmp_path, {"interpreter": OVERPASS_ANSWER}, seen=seen)
    query = FeatureQuery(area=Area(bbox=BOX), tags={"amenity": ["clinic", "hospital"]})
    result = providers.get("overpass").features(query)
    # The mock answers every query with all elements; only the one outside the area goes.
    ids = [r.id for r in result.items]
    assert ids == ["osm:node/1", "osm:way/2", "osm:way/3", "osm:relation/4"]
    assert result.data_timestamp == "2026-11-01T00:00:00Z"
    assert not result.truncated
    sent = parse_qs(seen[0].content.decode())["data"][0]
    assert '["amenity"~"^(clinic|hospital)$"]' in sent


def test_overpass_buildings_are_polygons_with_osm_refs(tmp_path: Path) -> None:
    providers = registry(tmp_path, {"interpreter": OVERPASS_ANSWER})
    result = providers.get("overpass").buildings(BuildingQuery(area=Area(bbox=BOX)))
    refs = [(b.ref.provider, b.ref.id) for b in result.items]
    assert refs == [("osm", "way/2"), ("osm", "relation/4")]


def test_overpass_partial_answers_are_refused(tmp_path: Path) -> None:
    answer = {"elements": [], "remark": 'runtime error: Query timed out in "query" at line 3'}
    providers = registry(tmp_path, {"interpreter": answer})
    with pytest.raises(GeoProductError) as caught:
        providers.get("overpass").features(FeatureQuery(area=Area(bbox=BOX), tags={"a": None}))
    assert caught.value.code == ErrorCode.timeout


PARTIAL = {
    "elements": [],
    "remark": 'runtime error: Query timed out in "query" at line 3 after 90 seconds.',
}


def in_turn(*answers: Any, seen: list[httpx2.Request]) -> httpx2.MockTransport:
    """Answers each request with the next answer; the last one repeats."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=answers[min(len(seen), len(answers)) - 1])

    return httpx2.MockTransport(handler)


def cached_bodies(workspace: Workspace) -> list[str]:
    return [p.read_text("utf-8") for p in (workspace.root / "cache" / "http").rglob("*.json")]


def test_overpass_partial_answer_does_not_poison_later_retries(tmp_path: Path) -> None:
    seen: list[httpx2.Request] = []
    workspace = Workspace(tmp_path).ensure()
    transport = in_turn(PARTIAL, OVERPASS_ANSWER, seen=seen)
    overpass = builtin_providers(workspace, AGENT, transport=transport).get("overpass")
    query = FeatureQuery(area=Area(bbox=BOX), tags={"amenity": None})

    with pytest.raises(GeoProductError) as caught:
        overpass.features(query)
    assert caught.value.code == ErrorCode.timeout
    assert not any("runtime error" in body for body in cached_bodies(workspace))

    retried = overpass.features(query)
    assert len(seen) == 2, "the retry reached the service, not a cached partial answer"
    assert retried.items
    assert not retried.from_cache

    again = overpass.features(query)
    assert again.from_cache, "a complete answer is still cached"
    assert len(seen) == 2


def test_overpass_answers_without_elements_are_refused_and_not_cached(tmp_path: Path) -> None:
    seen: list[httpx2.Request] = []
    workspace = Workspace(tmp_path).ensure()
    transport = in_turn({"osm3s": {}}, OVERPASS_ANSWER, seen=seen)
    overpass = builtin_providers(workspace, AGENT, transport=transport).get("overpass")
    query = FeatureQuery(area=Area(bbox=BOX), tags={"amenity": None})
    with pytest.raises(GeoProductError) as caught:
        overpass.features(query)
    assert caught.value.code == ErrorCode.provider_error
    assert overpass.features(query).items
    assert len(seen) == 2


def test_a_partial_answer_cached_by_an_earlier_version_is_asked_for_again(tmp_path: Path) -> None:
    seen: list[httpx2.Request] = []
    settings = HttpSettings(user_agent="test-suite/1.0", cache_dir=tmp_path / "cache")
    client = HttpClient(
        "overpass", settings, Throttle(None), transport=in_turn(PARTIAL, OVERPASS_ANSWER, seen=seen)
    )
    form = {"data": "[out:json];nwr;out;"}
    client.post_json("https://overpass.example.org/api/interpreter", form)  # cached unchecked

    result = client.post_json(
        "https://overpass.example.org/api/interpreter", form, check=overpass_incomplete
    )
    assert len(seen) == 2
    assert not result.from_cache
    assert result.data == OVERPASS_ANSWER


def test_a_partial_answer_is_retried_by_the_engine_and_the_run_succeeds(tmp_path: Path) -> None:
    seen: list[httpx2.Request] = []
    workspace = Workspace(tmp_path).ensure()
    providers = builtin_providers(
        workspace, AGENT, transport=in_turn(PARTIAL, OVERPASS_ANSWER, seen=seen)
    )
    runner = Runner(workspace, providers=providers)
    spec = validate_spec(
        {
            "apiVersion": "geoproduct/v1",
            "kind": "Pipeline",
            "name": "clinics",
            "stages": [
                {
                    "id": "clinics",
                    "uses": "collect.features",
                    "retry": {"max_attempts": 2, "backoff_s": 0},
                    "with": {
                        "provider": "overpass",
                        "area": {"bbox": list(BOX)},
                        "tags": {"amenity": None},
                    },
                }
            ],
        }
    )
    record = runner.run(spec)
    assert record.status == RunStatus.succeeded
    assert record.stages[0].attempts == 2
    assert len(seen) == 2


def test_overpass_marks_results_cut_off_at_the_limit(tmp_path: Path) -> None:
    providers = registry(tmp_path, {"interpreter": OVERPASS_ANSWER})
    query = FeatureQuery(area=Area(bbox=BOX), tags={"amenity": None}, limit=2)
    result = providers.get("overpass").features(query)
    assert result.truncated
    assert len(result.items) <= 2


# Through the engine ------------------------------------------------------------------------


def test_a_pipeline_collects_osm_features_with_provenance(tmp_path: Path) -> None:
    seen: list[httpx2.Request] = []
    providers = registry(tmp_path, {"interpreter": OVERPASS_ANSWER}, seen=seen)
    runner = Runner(Workspace(tmp_path), providers=providers)
    spec = validate_spec(
        {
            "apiVersion": "geoproduct/v1",
            "kind": "Pipeline",
            "name": "clinics",
            "stages": [
                {
                    "id": "clinics",
                    "uses": "collect.features",
                    "with": {
                        "provider": "overpass",
                        "area": {"bbox": list(BOX)},
                        "tags": {"amenity": "clinic"},
                    },
                }
            ],
        }
    )
    record = runner.run(spec)
    assert record.status == RunStatus.succeeded
    data = runner.output(record.run_id, "clinics")
    assert isinstance(data, RecordSet)
    assert data.sources[0].licence == "ODbL-1.0"
    assert "OpenStreetMap contributors" in (data.sources[0].attribution or "")
    rerun = runner.run(spec)
    assert rerun.status == RunStatus.succeeded
    assert len(seen) == 1, "the second run is served from the response cache"


def test_geocoding_leaves_unscored_alternatives_unresolved(tmp_path: Path) -> None:
    providers = registry(tmp_path, {"/search": NOMINATIM_SEARCH})
    runner = Runner(Workspace(tmp_path), providers=providers)
    spec = validate_spec(
        {
            "apiVersion": "geoproduct/v1",
            "kind": "Pipeline",
            "name": "geocode",
            "stages": [
                {
                    "id": "input",
                    "uses": "collect.records",
                    "with": {"records": [{"id": "a", "name": "Example Clinic"}]},
                },
                {
                    "id": "located",
                    "uses": "geocode.records",
                    "input": "input",
                    "with": {"provider": "nominatim"},
                },
            ],
        }
    )
    record = runner.run(spec)
    assert record.status == RunStatus.succeeded
    data = runner.output(record.run_id, "located")
    assert isinstance(data, RecordSet)
    (located,) = data.records
    assert located.geometry is None
    assert "ambiguous" in located.limitations[0]


def test_public_geocoding_refuses_bulk_runs(tmp_path: Path) -> None:
    providers = registry(tmp_path, {"/search": []})
    runner = Runner(Workspace(tmp_path), providers=providers)
    records = [{"id": f"r{i}", "name": f"Place {i}"} for i in range(101)]
    spec = validate_spec(
        {
            "apiVersion": "geoproduct/v1",
            "kind": "Pipeline",
            "name": "bulk",
            "stages": [
                {"id": "input", "uses": "collect.records", "with": {"records": records}},
                {
                    "id": "located",
                    "uses": "geocode.records",
                    "input": "input",
                    "with": {"provider": "nominatim"},
                },
            ],
        }
    )
    record = runner.run(spec)
    assert record.status == RunStatus.failed
    assert record.error is not None
    assert record.error.code == ErrorCode.policy_refused


@pytest.mark.parametrize(
    "url",
    [
        "https://user:hunter2@geo.example.org",
        "https://geo.example.org/search?key=hunter2",
        "ftp://geo.example.org",
        "geo.example.org",
        "https://geo.example.org:99999",
    ],
)
def test_endpoints_with_credentials_or_other_schemes_are_refused(tmp_path: Path, url: str) -> None:
    for variable in ("GEOPRODUCT_NOMINATIM_URL", "GEOPRODUCT_OVERPASS_URL"):
        with pytest.raises(GeoProductError) as caught:
            builtin_providers(Workspace(tmp_path), {variable: url})
        assert caught.value.code == ErrorCode.invalid_input
        assert "hunter2" not in caught.value.error.model_dump_json()


def test_the_public_instances_are_recognised_however_they_are_written(tmp_path: Path) -> None:
    env = {
        "GEOPRODUCT_NOMINATIM_URL": "http://Nominatim.OpenStreetMap.org/",
        "GEOPRODUCT_NOMINATIM_RATE": "50",
        "GEOPRODUCT_OVERPASS_URL": "https://OVERPASS-API.de/api/interpreter/",
    }
    providers = builtin_providers(Workspace(tmp_path), env)
    geocoder = providers.get("nominatim").info
    assert geocoder.policy.max_requests_per_second == 1.0
    assert geocoder.policy.max_batch == 100
    assert not geocoder.configured
    assert not providers.get("overpass").info.configured


def test_a_self_hosted_geocoder_can_go_faster(tmp_path: Path) -> None:
    env = {"GEOPRODUCT_NOMINATIM_URL": "http://localhost:8080/", "GEOPRODUCT_NOMINATIM_RATE": "20"}
    info = builtin_providers(Workspace(tmp_path), env).get("nominatim").info
    assert info.endpoint == "http://localhost:8080"
    assert info.policy.max_requests_per_second == 20
    assert info.policy.max_batch is None
    assert info.configured
