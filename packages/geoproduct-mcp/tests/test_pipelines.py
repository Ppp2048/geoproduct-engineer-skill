"""Pipeline tools: checking a spec, background runs, confirmation, resume and cancel."""

from pathlib import Path
from typing import Any

import pytest

from geoproduct.errors import GeoProductError
from geoproduct.schemas import ErrorCode, RunStatus
from geoproduct_mcp.tools import pipelines, quality
from geoproduct_mcp.tools.common import Done, Job, ServerState

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
