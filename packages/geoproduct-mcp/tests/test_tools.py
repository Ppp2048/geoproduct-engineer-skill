"""Each tool on its own, called directly (no MCP client), on the offline fixture town."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest

from geoproduct.engine.data import RecordSet
from geoproduct.errors import GeoProductError
from geoproduct.providers.fixture import AREA_BBOX
from geoproduct.schemas import Area, ErrorCode, Evidence, ExcerptCheck, SourceEvaluation, SourceType
from geoproduct_mcp.tools import geography, quality, records, sources
from geoproduct_mcp.tools.common import Done, Job, ServerState

AREA = Area(bbox=AREA_BBOX)
PAGE = "<html><body><p>Example Clinic opened its west wing in 2019 with 12 beds.</p></body></html>"


def out(done: Done) -> dict[str, Any]:
    """A tool result as the client receives it."""
    return done.output.model_dump(mode="json")


def fetch_answers(request: httpx2.Request) -> httpx2.Response:
    if request.url.path == "/robots.txt":
        return httpx2.Response(404)
    if request.url.path == "/clinic":
        return httpx2.Response(200, text=PAGE, headers={"content-type": "text/html"})
    if request.url.path == "/facilities.csv":
        body = "id,name,lon,lat\nf1,Example Clinic,0.0042,0.0031\nf2,Second Clinic,0.005,0.005\n"
        return httpx2.Response(200, text=body, headers={"content-type": "text/csv"})
    return httpx2.Response(404)


def register(state: ServerState, url: str, evaluation: SourceEvaluation | None = None) -> str:
    done = sources.register_source(
        state,
        title="Example source",
        type=SourceType.official_organisation,
        url_or_ref=url,
        licence="CC-BY-4.0",
        evaluation=evaluation,
    )
    return str(out(done)["source"]["id"])


# Sources -----------------------------------------------------------------------------------


def test_registering_computes_the_score_and_updates_in_place(
    state: ServerState, evaluation: Callable[..., SourceEvaluation]
) -> None:
    first = sources.register_source(
        state,
        title="Clinic site",
        type=SourceType.official_organisation,
        url_or_ref="https://example.org/clinic",
        evaluation=evaluation(3),
    )
    result = out(first)
    assert result["created"] is True
    assert result["source"]["score"] == 1.0
    assert result["source"]["tier"] == "high"
    assert "fetch_source_data" in first.summary
    again = sources.register_source(
        state,
        title="Clinic site",
        type=SourceType.official_organisation,
        url_or_ref="https://example.org/clinic",
        evaluation=evaluation(1),
    )
    assert out(again)["created"] is False
    assert out(again)["source"]["id"] == result["source"]["id"]
    assert out(again)["source"]["tier"] == "low"


def test_fetching_keeps_a_hashed_copy_and_its_text(state: ServerState, job: Job) -> None:
    state.fetch_transport = httpx2.MockTransport(fetch_answers)
    source_id = register(state, "https://example.org/clinic")
    result = out(sources.fetch_source_data(state, job, source_id=source_id))
    assert result["kind"] == "html"
    assert result["excerpt"].startswith("Example Clinic opened")
    assert len(result["source"]["content_sha256"]) == 64
    assert result["source"]["retrieved_at"] is not None
    for path in result["stored"]:
        assert (state.workspace.root / path).is_file()
    assert result["loadable"] is False


def test_excerpts_are_checked_against_fetched_text(state: ServerState, job: Job) -> None:
    state.fetch_transport = httpx2.MockTransport(fetch_answers)
    fetched = register(state, "https://example.org/clinic")
    sources.fetch_source_data(state, job, source_id=fetched)
    unfetched = register(state, "https://example.org/annual-report.pdf")
    evidence = [
        Evidence(
            id="ev-1",
            source_id=fetched,
            supports=["attributes.beds"],
            excerpt="opened its WEST wing in 2019 with 12 beds",
            excerpt_check=ExcerptCheck.not_checked,
        ),
        Evidence(
            id="ev-2",
            source_id=fetched,
            supports=["name"],
            excerpt="has 40 beds",
            excerpt_check=ExcerptCheck.verified,
        ),
        Evidence(id="ev-3", source_id=unfetched, supports=["name"], excerpt="Example Clinic"),
    ]
    rows = [
        {
            "id": "clinic-1",
            "name": "Example Clinic",
            "evidence_ids": ["ev-1", "ev-2", "ev-3"],
            "attributes": {"beds": {"value": 12, "evidence_ids": ["ev-1"]}},
        }
    ]
    done = records.create_dataset(state, job, records=rows, evidence=evidence)
    data = state.datasets.load(out(done)["dataset"]["dataset_id"])
    assert isinstance(data, RecordSet)
    checks = {e.id: e.excerpt_check for e in data.evidence}
    assert checks == {
        "ev-1": ExcerptCheck.verified,
        "ev-2": ExcerptCheck.not_found,
        "ev-3": ExcerptCheck.not_checked,
    }
    assert {s.id for s in data.sources} == {fetched, unfetched}
    assert "1 not found" in done.summary


def test_a_fetched_csv_loads_as_records_credited_to_its_source(
    state: ServerState, job: Job
) -> None:
    state.fetch_transport = httpx2.MockTransport(fetch_answers)
    source_id = register(state, "https://data.example.org/facilities.csv")
    fetched = out(sources.fetch_source_data(state, job, source_id=source_id))
    assert fetched["loadable"] is True
    done = records.create_dataset(state, job, source_id=source_id, name="facilities")
    data = state.datasets.load(out(done)["dataset"]["dataset_id"])
    assert isinstance(data, RecordSet)
    assert [r.name for r in data.records] == ["Example Clinic", "Second Clinic"]
    assert [s.id for s in data.sources] == [source_id]
    assert {e.source_id for e in data.evidence} == {source_id}


def test_create_dataset_needs_exactly_one_input(state: ServerState, job: Job) -> None:
    with pytest.raises(GeoProductError) as caught:
        records.create_dataset(state, job)
    assert caught.value.code == ErrorCode.invalid_input
    with pytest.raises(GeoProductError):
        records.create_dataset(state, job, records=[], file="x.csv")


def test_datasets_are_combined_with_their_sources(state: ServerState, job: Job) -> None:
    mapped = out(
        geography.search_osm_features(
            state, job, area=AREA, tags={"amenity": "clinic"}, provider="fixture"
        )
    )
    mapped_data = state.datasets.load(mapped["dataset"]["dataset_id"])
    assert isinstance(mapped_data, RecordSet)
    researched = out(
        records.create_dataset(
            state,
            job,
            records=[
                {"id": "list-1", "name": "Listed Clinic"},
                {"id": mapped_data.records[0].id, "name": "Same id as a mapped clinic"},
            ],
        )
    )
    combined = out(
        records.create_dataset(
            state,
            job,
            dataset_ids=[mapped["dataset"]["dataset_id"], researched["dataset"]["dataset_id"]],
        )
    )
    dataset = combined["dataset"]
    assert dataset["item_count"] == len(mapped_data.records) + 1
    assert dataset["rejected"] == 1
    data = state.datasets.load(dataset["dataset_id"])
    assert isinstance(data, RecordSet)
    assert {s.id for s in mapped_data.sources} <= {s.id for s in data.sources}
    with pytest.raises(GeoProductError):
        records.create_dataset(state, job, dataset_ids=[mapped["dataset"]["dataset_id"]] * 2)


def test_invalid_records_are_rejected_with_reasons(state: ServerState, job: Job) -> None:
    rows = [{"id": "ok", "name": "A"}, {"id": "bad", "geometry": {"type": "Point"}}]
    done = records.create_dataset(state, job, records=rows)
    dataset = out(done)["dataset"]
    assert (dataset["item_count"], dataset["rejected"]) == (1, 1)
    assert (state.workspace.root / dataset["folder"] / "rejects.jsonl").is_file()


# Geography ---------------------------------------------------------------------------------


def test_geocoding_through_a_live_provider_reports_attribution(state: ServerState) -> None:
    done = geography.geocode(state, text="Example Clinic", provider="nominatim")
    result = out(done)
    assert result["items"][0]["provider_ref"] == "node/7"
    assert result["licence"] == "ODbL-1.0"
    assert "OpenStreetMap contributors" in done.summary


def test_unknown_providers_are_named(state: ServerState) -> None:
    with pytest.raises(GeoProductError) as caught:
        geography.geocode(state, text="x", provider="nowhere")
    assert caught.value.code == ErrorCode.not_found
    assert "fixture" in caught.value.error.details["available"]  # type: ignore[operator]


def test_records_are_geocoded_with_the_geocoder_cited(state: ServerState, job: Job) -> None:
    rows = [
        {"id": "a", "name": "Example Clinic", "address": {"full": "1 Example Road, Exampleton"}},
        {"id": "b", "name": "Placed Already", "geometry": {"type": "Point", "coordinates": [0, 0]}},
    ]
    dataset_id = out(records.create_dataset(state, job, records=rows))["dataset"]["dataset_id"]
    done = geography.geocode_records(state, job, dataset_id=dataset_id, provider="nominatim")
    data = state.datasets.load(out(done)["dataset"]["dataset_id"])
    assert isinstance(data, RecordSet)
    located = {r.id: r for r in data.records}
    clinic = located["a"]
    assert clinic.geometry is not None
    assert clinic.geometry.model_dump()["coordinates"] == [0.0042, 0.0031]
    assert clinic.location.method == "geocode:nominatim"
    cited = {e.id: e.source_id for e in data.evidence}
    licences = {s.id: s.licence for s in data.sources}
    assert [licences[cited[e]] for e in clinic.location.evidence_ids] == ["ODbL-1.0"]
    placed = located["b"].geometry
    assert placed is not None
    assert placed.model_dump()["coordinates"] == [0, 0], "a located record is left alone"


def test_geocoding_many_records_online_needs_confirmation(
    state: ServerState, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [{"id": f"r{i}", "name": f"Place {i}"} for i in range(26)]
    dataset_id = out(records.create_dataset(state, job, records=rows))["dataset"]["dataset_id"]
    with pytest.raises(GeoProductError) as caught:
        geography.geocode_records(state, job, dataset_id=dataset_id, provider="nominatim")
    assert caught.value.code == ErrorCode.confirmation_required
    assert caught.value.error.details == {"requests": 26, "threshold": 25, "provider": "nominatim"}
    # The offline fixture makes no requests, so it needs no confirmation.
    offline = out(geography.geocode_records(state, job, dataset_id=dataset_id, provider="fixture"))
    assert offline["dataset"]["item_count"] == 26

    # With confirm=true the run goes ahead (threshold lowered so one request is enough).
    monkeypatch.setattr(geography, "CONFIRM_ABOVE_CALLS", 0)
    one = out(records.create_dataset(state, job, records=[{"id": "x", "name": "Example Clinic"}]))
    one_id = one["dataset"]["dataset_id"]
    with pytest.raises(GeoProductError):
        geography.geocode_records(state, job, dataset_id=one_id, provider="nominatim")
    done = geography.geocode_records(
        state, job, dataset_id=one_id, provider="nominatim", confirm=True
    )
    assert out(done)["dataset"]["item_count"] == 1


def test_kind_mismatches_are_explained(state: ServerState, job: Job) -> None:
    buildings = out(geography.get_building_footprints(state, job, area=AREA, provider="fixture"))
    with pytest.raises(GeoProductError) as caught:
        records.normalize_records(state, job, dataset_id=buildings["dataset"]["dataset_id"])
    assert caught.value.code == ErrorCode.invalid_input
    assert "holds buildings" in caught.value.error.message


def test_invalid_stage_options_list_the_problems(state: ServerState, job: Job) -> None:
    clinics = out(
        geography.search_osm_features(
            state, job, area=AREA, tags={"amenity": None}, provider="fixture"
        )
    )
    with pytest.raises(GeoProductError) as caught:
        records.enrich_records(
            state,
            job,
            dataset_id=clinics["dataset"]["dataset_id"],
            operation="measure",
            options={"measures": ["volume"]},
        )
    assert caught.value.code == ErrorCode.invalid_input
    assert caught.value.error.details["problems"]
    with pytest.raises(GeoProductError):
        records.enrich_records(
            state, job, dataset_id=clinics["dataset"]["dataset_id"], operation="nearby"
        )


# A whole research flow ---------------------------------------------------------------------


def test_collect_resolve_match_validate_report_and_export(
    state: ServerState, job: Job, tmp_path: Path
) -> None:
    hospitals = out(
        geography.search_osm_features(
            state,
            job,
            area=AREA,
            tags={"amenity": ["hospital", "clinic"]},
            provider="fixture",
            name="health",
        )
    )
    assert hospitals["attribution"] == [
        "Synthetic data generated by geoproduct-core. Not a real place."
    ]
    ids = {"input": hospitals["dataset"]["dataset_id"]}
    ids["normal"] = out(records.normalize_records(state, job, dataset_id=ids["input"]))["dataset"][
        "dataset_id"
    ]
    deduped = out(records.deduplicate_entities(state, job, dataset_id=ids["normal"]))
    assert deduped["dataset"]["item_count"] < hospitals["dataset"]["item_count"]
    buildings = out(geography.get_building_footprints(state, job, area=AREA, provider="fixture"))
    matched = out(
        records.match_building_footprints(
            state,
            job,
            dataset_id=deduped["dataset"]["dataset_id"],
            buildings_dataset_id=buildings["dataset"]["dataset_id"],
        )
    )
    statuses = {item.get("building_match") for item in matched["preview"]}
    assert statuses <= {"exact", "probable", "ambiguous", "none"}
    districts = out(
        geography.search_osm_features(
            state, job, area=AREA, tags={"place": "neighbourhood"}, provider="fixture"
        )
    )
    within = out(
        records.enrich_records(
            state,
            job,
            dataset_id=matched["dataset"]["dataset_id"],
            operation="within",
            reference_dataset_id=districts["dataset"]["dataset_id"],
            field="district",
        )
    )
    scored = out(records.score_confidence(state, job, dataset_id=within["dataset"]["dataset_id"]))
    assert all("confidence" in item for item in scored["preview"])
    validation = quality.validate_dataset(
        state,
        job,
        dataset_id=scored["dataset"]["dataset_id"],
        area=AREA,
        rules=[
            quality.RuleInput(
                name="in-a-district",
                kind="within",
                reference_dataset_id=districts["dataset"]["dataset_id"],
                severity="warning",
            )
        ],
        name="health",
    )
    report = out(validation)
    assert 0 <= report["score"] <= 1
    assert report["dataset"]["kind"] == "report"
    written = out(
        quality.generate_quality_report(
            state, job, report_dataset_id=report["dataset"]["dataset_id"], name="health-report"
        )
    )
    assert written["markdown"].startswith("#")
    exports = tmp_path / "exports"
    exported = quality.export_geo_dataset(
        state,
        job,
        dataset_id=scored["dataset"]["dataset_id"],
        format="geojson",
        name="health",
        copy_to=str(exports),
    )
    assert (exports / "health.geojson").is_file()
    assert (exports / "health.manifest.json").is_file()
    assert out(exported)["dataset"]["kind"] == "files"
    csv = out(
        quality.export_geo_dataset(
            state, job, dataset_id=scored["dataset"]["dataset_id"], format="csv", name="health"
        )
    )
    assert csv["dataset"]["files"][0]["path"] == "health.csv"


def test_exports_outside_allowed_folders_are_refused(
    state: ServerState, job: Job, tmp_path: Path
) -> None:
    clinics = out(
        geography.search_osm_features(
            state, job, area=AREA, tags={"amenity": "clinic"}, provider="fixture"
        )
    )
    with pytest.raises(GeoProductError) as caught:
        quality.export_geo_dataset(
            state,
            job,
            dataset_id=clinics["dataset"]["dataset_id"],
            format="csv",
            copy_to=str(tmp_path / "elsewhere"),
        )
    assert caught.value.code == ErrorCode.path_not_allowed
    leftovers = [p.name for p in (state.workspace.root / "datasets").iterdir()]
    assert leftovers == [clinics["dataset"]["dataset_id"]], "failed tools leave no dataset"
