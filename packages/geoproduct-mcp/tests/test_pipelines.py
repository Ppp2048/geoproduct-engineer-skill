"""Pipeline tools: checking a spec, background runs, confirmation, resume and cancel."""

from collections import Counter
from pathlib import Path
from typing import Any

import httpx2
import pytest

from geoproduct.engine import data as stage_data
from geoproduct.engine.data import DataKind
from geoproduct.errors import GeoProductError
from geoproduct.providers.registry import builtin_providers
from geoproduct.schemas import ErrorCode, RunStatus
from geoproduct.workspace import Workspace
from geoproduct_mcp.tools import pipelines, quality
from geoproduct_mcp.tools.common import Done, Job, ServerState

AGENT = {"GEOPRODUCT_USER_AGENT": "test-suite/1.0 (tests@example.org)"}

REPO_ROOT = Path(__file__).resolve().parents[3]
HOSPITALS = str(REPO_ROOT / "examples" / "pipelines" / "hospitals.yaml")
TRANSPORT = str(REPO_ROOT / "examples" / "pipelines" / "transport.yaml")

GEOCODE_SPEC = {
    "apiVersion": "geoproduct/v1",
    "kind": "Pipeline",
    "name": "locate-clinics",
    "stages": [
        {
            "id": "input",
            "uses": "collect.records",
            "with": {"records": [{"id": "c1", "name": "Example Clinic"}]},
        },
        {
            "id": "located",
            "uses": "geocode.records",
            "input": "input",
            "with": {"provider": "nominatim"},
        },
    ],
}


def out(done: Done) -> dict[str, Any]:
    return done.output.model_dump(mode="json")


def finish(state: ServerState, run_id: str) -> dict[str, Any]:
    assert state.jobs.wait(run_id, timeout=60), "the run did not finish"
    return out(pipelines.get_pipeline_status(state, run_id=run_id))


def test_checking_a_spec_records_a_pending_run_with_its_plan(state: ServerState) -> None:
    plan = out(pipelines.create_geo_pipeline(state, spec_path=HOSPITALS))
    assert plan["status"] == "pending"
    assert plan["pipeline_name"] == "hospitals"
    assert plan["stages"][0]["uses"] == "collect.features"
    assert plan["estimate"] == {
        "network_calls": 0,
        "unbounded": [],
        "needs_confirmation": False,
        "threshold": 25,
        "stages": [],
    }


def test_a_run_finishes_in_the_background_and_its_outputs_are_datasets(
    state: ServerState, job: Job
) -> None:
    started = out(pipelines.run_geo_pipeline(state, spec_path=HOSPITALS))
    run_id = started["run_id"]
    status = finish(state, run_id)
    assert status["run"]["status"] == "succeeded"
    assert status["active"] is False
    results = out(pipelines.get_pipeline_results(state, run_id=run_id))
    outputs = {o["stage_id"]: o for o in results["outputs"]}
    scored = next(o for o in outputs.values() if o["uses"] == "quality.confidence")
    assert scored["dataset"]["dataset_id"] == f"{run_id}/{scored['stage_id']}"
    assert scored["preview"]
    exported = out(
        quality.export_geo_dataset(
            state, job, dataset_id=scored["dataset"]["dataset_id"], format="csv", name="from-run"
        )
    )
    assert exported["dataset"]["inputs"] == [scored["dataset"]["dataset_id"]]


def test_results_preview_outputs_without_loading_them_whole(
    state: ServerState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit GP-09: every stage's whole output was loaded to show five items."""
    run_id = out(pipelines.run_geo_pipeline(state, spec_path=HOSPITALS))["run_id"]
    assert finish(state, run_id)["run"]["status"] == "succeeded"
    expected = {
        o["stage_id"]: o["preview"]
        for o in out(pipelines.get_pipeline_results(state, run_id=run_id))["outputs"]
    }
    whole = stage_data.load

    def load_small_only(kind: DataKind, folder: Path) -> Any:
        assert kind not in (DataKind.records, DataKind.buildings), f"{folder.name} loaded whole"
        return whole(kind, folder)

    monkeypatch.setattr(stage_data, "load", load_small_only)
    results = out(pipelines.get_pipeline_results(state, run_id=run_id))
    previews = {o["stage_id"]: o["preview"] for o in results["outputs"]}
    assert previews == expected
    kinds = {o["dataset"]["kind"] for o in results["outputs"]}
    assert {"records", "buildings"} <= kinds
    assert all(len(p) <= 5 for p in previews.values())


def test_a_failed_run_is_resumed_with_corrected_parameters(state: ServerState) -> None:
    started = out(
        pipelines.run_geo_pipeline(state, spec_path=TRANSPORT, params={"max_stop_distance_m": 5})
    )
    run_id = started["run_id"]
    failed = finish(state, run_id)
    assert failed["run"]["status"] == "failed"
    assert failed["note"].startswith("Failed:")
    assert f"{run_id}/validation" in failed["finished_outputs"]
    out(pipelines.run_geo_pipeline(state, run_id=run_id, params={"max_stop_distance_m": 30}))
    assert finish(state, run_id)["run"]["status"] == "succeeded"
    with pytest.raises(GeoProductError) as caught:
        pipelines.run_geo_pipeline(state, run_id=run_id)
    assert "already succeeded" in caught.value.error.message


BOX = [0.0, 0.0, 0.01, 0.01]
CLINIC = {
    "type": "node",
    "id": 11,
    "lat": 0.0031,
    "lon": 0.0042,
    "tags": {"amenity": "clinic", "name": "Example Clinic"},
}
BUILDING = {
    "type": "way",
    "id": 21,
    "tags": {"building": "yes", "name": "Example Clinic"},
    "geometry": [
        {"lon": lon, "lat": lat}
        for lon, lat in [
            (0.0040, 0.0029),
            (0.0044, 0.0029),
            (0.0044, 0.0033),
            (0.0040, 0.0033),
            (0.0040, 0.0029),
        ]
    ],
}
CLINIC_BUILDINGS = {
    "apiVersion": "geoproduct/v1",
    "kind": "Pipeline",
    "name": "clinic-buildings",
    "stages": [
        {
            "id": "clinics",
            "uses": "collect.features",
            "with": {"provider": "overpass", "area": {"bbox": BOX}, "tags": {"amenity": "clinic"}},
        },
        {
            "id": "buildings",
            "uses": "collect.buildings",
            "with": {"provider": "overpass", "area": {"bbox": BOX}},
        },
        {
            "id": "matched",
            "uses": "match.buildings",
            "input": "clinics",
            "with": {"buildings": {"stage": "buildings"}},
        },
        {"id": "export", "uses": "export.geojson", "input": "matched"},
    ],
}


def test_reused_stages_count_as_finished_after_a_resume(workspace: Workspace) -> None:
    """The buildings query fails twice (HTTP 400), so the run is resumed twice. The clinics
    stage runs once and is then reused from its checkpoint; status, results and the call
    estimate must all count it as finished."""
    asked: Counter[str] = Counter()

    def overpass(request: httpx2.Request) -> httpx2.Response:
        kind = "buildings" if "building" in request.content.decode() else "features"
        asked[kind] += 1
        if kind == "buildings" and asked[kind] <= 2:
            return httpx2.Response(400, text="bad request")
        found = BUILDING if kind == "buildings" else CLINIC
        return httpx2.Response(200, json={"elements": [found]})

    env = {**AGENT, "GEOPRODUCT_CACHE_TTL_HOURS": "0"}  # reuse must come from checkpoints
    providers = builtin_providers(workspace, env, transport=httpx2.MockTransport(overpass))
    state = ServerState(workspace, providers=providers, env=env)

    run_id = out(pipelines.run_geo_pipeline(state, spec=CLINIC_BUILDINGS))["run_id"]
    assert finish(state, run_id)["run"]["status"] == "failed"
    out(pipelines.run_geo_pipeline(state, run_id=run_id))
    failed_again = finish(state, run_id)
    assert failed_again["run"]["status"] == "failed"
    assert failed_again["run"]["stages"][0]["status"] == "skipped"
    assert failed_again["finished_outputs"] == [f"{run_id}/clinics"]

    resumed = out(pipelines.run_geo_pipeline(state, run_id=run_id))
    assert [s["stage_id"] for s in resumed["estimate"]["stages"]] == ["buildings"]
    assert state.jobs.wait(run_id, timeout=60)
    status = pipelines.get_pipeline_status(state, run_id=run_id)
    stages = ["clinics", "buildings", "matched", "export"]
    assert out(status)["run"]["status"] == "succeeded"
    assert out(status)["finished_outputs"] == [f"{run_id}/{s}" for s in stages]
    assert "4 of 4 stage(s) done, 1 reused from a checkpoint" in status.summary

    results = pipelines.get_pipeline_results(state, run_id=run_id)
    assert [o["stage_id"] for o in out(results)["outputs"]] == stages
    clinics = out(results)["outputs"][0]
    assert clinics["dataset"]["item_count"] == 1
    assert clinics["preview"][0]["name"] == "Example Clinic"
    assert "4 of 4 stage(s) done" in results.summary
    assert asked == {"features": 1, "buildings": 3}, "no finished stage ran again"


def test_many_external_requests_need_confirmation(state: ServerState) -> None:
    with pytest.raises(GeoProductError) as caught:
        pipelines.run_geo_pipeline(state, spec=GEOCODE_SPEC)
    error = caught.value.error
    assert error.code == ErrorCode.confirmation_required
    estimate = error.details["estimate"]
    assert isinstance(estimate, dict)
    assert estimate["network_calls"] == 100
    assert estimate["stages"][0]["max_calls"] == 100  # type: ignore[index]
    run_id = str(error.details["run_id"])
    assert state.runner.status(run_id).status == RunStatus.pending
    out(pipelines.run_geo_pipeline(state, run_id=run_id, confirm=True))
    assert finish(state, run_id)["run"]["status"] == "succeeded"


def test_repeated_unconfirmed_calls_leave_one_pending_run(state: ServerState) -> None:
    """Audit GP-06: every unconfirmed call recorded a new pending run. A retry now reuses the
    run its earlier attempt recorded, and the confirmed call runs that one."""
    run_ids = set()
    for _ in range(3):
        with pytest.raises(GeoProductError) as caught:
            pipelines.run_geo_pipeline(state, spec=GEOCODE_SPEC)
        run_ids.add(str(caught.value.error.details["run_id"]))
    assert len(run_ids) == 1
    assert state.runner.store.run_ids() == sorted(run_ids)
    started = out(pipelines.run_geo_pipeline(state, spec=GEOCODE_SPEC, confirm=True))
    assert {started["run_id"]} == run_ids
    assert finish(state, started["run_id"])["run"]["status"] == "succeeded"
    # A finished run is never reused: the same spec again records a new one.
    with pytest.raises(GeoProductError) as caught:
        pipelines.run_geo_pipeline(state, spec=GEOCODE_SPEC)
    assert str(caught.value.error.details["run_id"]) not in run_ids
    assert len(state.runner.store.run_ids()) == 2


def test_checking_the_same_spec_again_reuses_its_pending_run(state: ServerState) -> None:
    first = out(pipelines.create_geo_pipeline(state, spec_path=HOSPITALS))["run_id"]
    again = out(pipelines.create_geo_pipeline(state, spec_path=HOSPITALS))["run_id"]
    other = pipelines.create_geo_pipeline(state, spec_path=HOSPITALS, params={"output_name": "x"})
    assert again == first
    assert out(other)["run_id"] != first, "other parameters make another run"


def test_a_misconfigured_provider_records_no_run(state: ServerState) -> None:
    """Audit GP-06: the provider check came after the run was recorded."""
    unconfigured = ServerState(state.workspace, env={})
    with pytest.raises(GeoProductError) as caught:
        pipelines.run_geo_pipeline(unconfigured, spec=GEOCODE_SPEC, confirm=True)
    assert caught.value.code == ErrorCode.auth_missing
    assert unconfigured.runner.store.run_ids() == []


def test_unconfigured_providers_are_reported_before_running(state: ServerState) -> None:
    unconfigured = ServerState(state.workspace, env={})
    plan = out(pipelines.create_geo_pipeline(unconfigured, spec=GEOCODE_SPEC))
    assert "not configured" in plan["warnings"][0]
    with pytest.raises(GeoProductError) as caught:
        pipelines.run_geo_pipeline(unconfigured, run_id=plan["run_id"], confirm=True)
    assert caught.value.code == ErrorCode.auth_missing


def test_parameters_must_be_declared(state: ServerState) -> None:
    with pytest.raises(GeoProductError) as caught:
        pipelines.create_geo_pipeline(state, spec_path=HOSPITALS, params={"nope": 1})
    assert caught.value.code == ErrorCode.invalid_input


def test_a_spec_is_given_one_way(state: ServerState) -> None:
    with pytest.raises(GeoProductError):
        pipelines.create_geo_pipeline(state)
    with pytest.raises(GeoProductError):
        pipelines.create_geo_pipeline(state, spec=GEOCODE_SPEC, spec_path=HOSPITALS)


def test_specs_outside_allowed_folders_are_refused(state: ServerState, tmp_path: Path) -> None:
    outside = tmp_path / "spec.yaml"
    outside.write_text("name: x\n", encoding="utf-8")
    with pytest.raises(GeoProductError) as caught:
        pipelines.create_geo_pipeline(state, spec_path=str(outside))
    assert caught.value.code == ErrorCode.path_not_allowed


def test_yaml_text_is_accepted(state: ServerState) -> None:
    text = Path(HOSPITALS).read_text(encoding="utf-8")
    assert out(pipelines.create_geo_pipeline(state, spec=text))["pipeline_name"] == "hospitals"


def test_a_cancelled_pending_run_can_be_started_later(state: ServerState) -> None:
    run_id = out(pipelines.create_geo_pipeline(state, spec_path=HOSPITALS))["run_id"]
    cancelled = pipelines.cancel_geo_pipeline(state, run_id=run_id)
    assert out(cancelled)["run"]["status"] == "cancelled"
    out(pipelines.run_geo_pipeline(state, run_id=run_id))
    assert finish(state, run_id)["run"]["status"] == "succeeded"
    done = pipelines.cancel_geo_pipeline(state, run_id=run_id)
    assert "nothing to cancel" in done.summary


def test_unknown_runs_are_not_found(state: ServerState) -> None:
    with pytest.raises(GeoProductError) as caught:
        pipelines.get_pipeline_status(state, run_id="run_00000000000000000000000000")
    assert caught.value.code == ErrorCode.not_found
