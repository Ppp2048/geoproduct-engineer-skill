"""Pipelines: checking a spec, running it in the background, and reading its results.

A pipeline is addressed by the ``run_id`` of its run. ``create_geo_pipeline`` checks the
spec and records a pending run without running anything; ``run_geo_pipeline`` starts a
pending run, resumes a failed or cancelled one, or creates and starts a run in one call.

Before a run starts, its external calls are estimated. When the estimate is above
:data:`CONFIRM_ABOVE_CALLS`, or cannot be bounded (geocoding with no batch limit), the run
needs ``confirm: true``, so the agent has to check with the user first.
"""

from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import Field, JsonValue

from geoproduct.engine.spec import Plan, load_spec_file, parse_spec, validate_spec, with_params
from geoproduct.errors import GeoProductError
from geoproduct.providers.base import Capability
from geoproduct.schemas import Contract, ErrorCode, PipelineSpec, RunRecord, RunStatus, StageStatus
from geoproduct.schemas._base import ShortText, Slug, Text
from geoproduct_mcp.store import DatasetInfo
from geoproduct_mcp.tools.common import (
    CONFIRM_ABOVE_CALLS,
    Done,
    ServerState,
    invalid,
    preview,
)

ONE_REQUEST_STAGES = frozenset({"collect.features", "collect.buildings"})
STAGE_CAPABILITY = {
    "collect.features": Capability.features,
    "collect.buildings": Capability.buildings,
    "geocode.records": Capability.geocode,
}


class StageCalls(Contract):
    stage_id: Slug
    uses: ShortText
    provider: Slug
    calls: Annotated[int, Field(ge=0)] | None
    """Requests this stage makes, when known in advance."""
    max_calls: Annotated[int, Field(ge=0)] | None
    """The most it can make, when only a limit is known."""
    note: Text


class CallEstimate(Contract):
    network_calls: Annotated[int, Field(ge=0)]
    """Known requests plus upper limits; cached responses make the real number smaller."""
    unbounded: list[Slug]
    """Stages whose number of requests depends on the data and has no limit."""
    needs_confirmation: bool
    threshold: int
    stages: list[StageCalls]


class PipelinePlan(Contract):
    run_id: ShortText
    pipeline_name: Slug
    status: RunStatus
    params: dict[str, JsonValue]
    stages: list[dict[str, JsonValue]]
    estimate: CallEstimate
    warnings: list[Text] = Field(default_factory=list)


class RunStarted(Contract):
    run_id: ShortText
    status: RunStatus
    estimate: CallEstimate


class RunStatusResult(Contract):
    run: RunRecord
    active: bool
    """Whether this server is executing the run now."""
    finished_outputs: list[ShortText]
    """Dataset ids (``<run_id>/<stage_id>``) of stages with output, usable in other tools."""
    note: Text | None = None


class StageOutput(Contract):
    stage_id: Slug
    uses: ShortText
    dataset: DatasetInfo
    preview: list[dict[str, JsonValue]]


class RunResults(Contract):
    run_id: ShortText
    status: RunStatus
    outputs: list[StageOutput]


def load_spec(
    state: ServerState, spec: Mapping[str, Any] | str | None, spec_path: str | None
) -> PipelineSpec:
    if (spec is None) == (spec_path is None):
        raise invalid("Give exactly one of spec (an object, YAML or JSON text) or spec_path.")
    if spec_path is not None:
        return load_spec_file(state.workspace.user_path(spec_path))
    if isinstance(spec, str):
        return parse_spec(spec)
    assert spec is not None
    return validate_spec(spec)


def estimate(
    state: ServerState, plan: Plan, skip: frozenset[str] = frozenset()
) -> tuple[CallEstimate, list[str]]:
    """External calls the plan's stages may make, and warnings about unusable providers."""
    stages: list[StageCalls] = []
    warnings: list[str] = []
    for planned in plan.stages:
        name = getattr(planned.params, "provider", None)
        if planned.id in skip or not isinstance(name, str):
            continue
        provider = state.providers.get(name)
        info = provider.info
        if not info.configured:
            note = f"Stage '{planned.id}': provider '{name}' is not configured."
            warnings.append(f"{note} {info.status_note or ''}".strip())
        if not info.network:
            continue
        uses = planned.definition.name
        if uses in ONE_REQUEST_STAGES:
            calls, limit, note = 1, None, "one query for the whole area"
        elif uses == "geocode.records":
            calls, limit = None, info.policy.max_batch
            note = "one request per record without a location"
            if limit is not None:
                note += f"; the provider allows at most {limit} per run"
        else:
            calls, limit, note = None, None, "depends on the input"
        stages.append(
            StageCalls(
                stage_id=planned.id,
                uses=uses,
                provider=name,
                calls=calls,
                max_calls=limit,
                note=note,
            )
        )
    total = sum((s.calls if s.calls is not None else s.max_calls or 0) for s in stages)
    unbounded = [s.stage_id for s in stages if s.calls is None and s.max_calls is None]
    return (
        CallEstimate(
            network_calls=total,
            unbounded=unbounded,
            needs_confirmation=bool(unbounded) or total > CONFIRM_ABOVE_CALLS,
            threshold=CONFIRM_ABOVE_CALLS,
            stages=stages,
        ),
        warnings,
    )


def _plan_output(state: ServerState, record: RunRecord, spec: PipelineSpec) -> PipelinePlan:
    plan = state.runner.plan(spec)
    calls, warnings = estimate(state, plan)
    return PipelinePlan(
        run_id=record.run_id,
        pipeline_name=record.pipeline_name,
        status=record.status,
        params=dict(spec.params),
        stages=plan.describe(),
        estimate=calls,
        warnings=warnings,
    )


def create_geo_pipeline(
    state: ServerState,
    *,
    spec: Mapping[str, Any] | str | None = None,
    spec_path: str | None = None,
    params: Mapping[str, JsonValue] | None = None,
) -> Done:
    parsed = with_params(load_spec(state, spec, spec_path), params or {})
    record = state.runner.create(parsed)
    output = _plan_output(state, record, parsed)
    calls = output.estimate
    summary = (
        f"Pipeline '{parsed.name}' is valid: {len(output.stages)} stage(s), recorded as "
        f"pending run {record.run_id}. Estimated external requests: {calls.network_calls}"
    )
    summary += f" (unbounded: {', '.join(calls.unbounded)})." if calls.unbounded else "."
    if calls.needs_confirmation:
        summary += " Running it needs confirm=true; check the estimate with the user first."
    for warning in output.warnings:
        summary += f" Warning: {warning}"
    return Done(output, summary)


def run_geo_pipeline(
    state: ServerState,
    *,
    run_id: str | None = None,
    spec: Mapping[str, Any] | str | None = None,
    spec_path: str | None = None,
    params: Mapping[str, JsonValue] | None = None,
    confirm: bool = False,
) -> Done:
    changes: Mapping[str, JsonValue] | None = None
    if run_id is not None:
        if spec is not None or spec_path is not None:
            raise invalid("Give either run_id or a spec, not both.")
        record = state.runner.status(run_id)
        if state.jobs.active(run_id):
            raise invalid(
                "This run is already executing.",
                hint="Poll get_pipeline_status, or cancel it first.",
                run_id=run_id,
            )
        current = state.runner.store.load_spec(run_id)
        new_spec = with_params(current, params or {})
        if record.status == RunStatus.succeeded and new_spec == current:
            raise invalid(
                "This run already succeeded and no parameter changed.",
                hint="Read get_pipeline_results, or pass params to run it with new values.",
                run_id=run_id,
            )
        changes = params or None
        finished = (
            frozenset(s.stage_id for s in record.stages if s.status == StageStatus.succeeded)
            if new_spec == current
            else frozenset()
        )
        calls, _ = estimate(state, state.runner.plan(new_spec), finished)
    else:
        new_spec = with_params(load_spec(state, spec, spec_path), params or {})
        calls, _ = estimate(state, state.runner.plan(new_spec))
        run_id = state.runner.create(new_spec).run_id
    for stage in calls.stages:
        if capability := STAGE_CAPABILITY.get(stage.uses):
            state.providers.require(stage.provider, capability)
    if calls.needs_confirmation and not confirm:
        raise GeoProductError(
            ErrorCode.confirmation_required,
            f"This run may make {calls.network_calls} external request(s)"
            + (
                f", plus an unbounded number in {', '.join(calls.unbounded)}"
                if calls.unbounded
                else ""
            )
            + "; it needs explicit confirmation.",
            details={"run_id": run_id, "estimate": calls.model_dump(mode="json")},
            hint=f"Show the estimate to the user. If they agree, call run_geo_pipeline with "
            f"run_id='{run_id}' and confirm=true.",
        )
    state.jobs.start(run_id, changes)
    status = state.runner.status(run_id).status
    output = RunStarted(run_id=run_id, status=status, estimate=calls)
    summary = (
        f"Started run {run_id} in the background. Poll get_pipeline_status('{run_id}') "
        "until it finishes."
    )
    return Done(output, summary)


def get_pipeline_status(state: ServerState, *, run_id: str) -> Done:
    record = state.runner.status(run_id)
    active = state.jobs.active(run_id)
    outputs = [f"{run_id}/{s.stage_id}" for s in record.stages if s.status == StageStatus.succeeded]
    note = None
    if record.status == RunStatus.running and not active:
        note = (
            "This server is not executing the run. If no other process is, it was "
            "interrupted: run_geo_pipeline(run_id=...) resumes it from its last finished stage."
        )
    elif record.status == RunStatus.failed and record.error is not None:
        note = f"Failed: {record.error.message}"
        if record.error.hint:
            note += f" {record.error.hint}"
    result = RunStatusResult(run=record, active=active, finished_outputs=outputs, note=note)
    done = sum(1 for s in record.stages if s.status == StageStatus.succeeded)
    summary = f"Run {run_id} is {record.status.value}: {done} of {len(record.stages)} stage(s) done"
    if record.current_stage and record.status == RunStatus.running:
        summary += f", now running '{record.current_stage}'"
    summary += "."
    if note:
        summary += f" {note}"
    return Done(result, summary)


def get_pipeline_results(state: ServerState, *, run_id: str) -> Done:
    record = state.runner.status(run_id)
    outputs: list[StageOutput] = []
    for stage in record.stages:
        if stage.status != StageStatus.succeeded:
            continue
        dataset_id = f"{run_id}/{stage.stage_id}"
        info = state.datasets.info(dataset_id)
        outputs.append(
            StageOutput(
                stage_id=stage.stage_id,
                uses=stage.uses,
                dataset=info,
                preview=preview(state.datasets.load(dataset_id)),
            )
        )
    result = RunResults(run_id=run_id, status=record.status, outputs=outputs)
    listed = ", ".join(f"{o.stage_id} ({o.dataset.item_count})" for o in outputs) or "none"
    summary = f"Run {run_id} is {record.status.value}. Stage outputs: {listed}."
    return Done(result, summary)


def cancel_geo_pipeline(state: ServerState, *, run_id: str) -> Done:
    record = state.runner.cancel(run_id)
    if record.status in (RunStatus.succeeded, RunStatus.failed):
        summary = f"Run {run_id} had already {record.status.value}; nothing to cancel."
    else:
        summary = (
            f"Cancellation requested for run {run_id}. The current stage stops at its next "
            "check; run_geo_pipeline(run_id=...) resumes it later."
        )
    active = state.jobs.active(run_id)
    return Done(
        RunStatusResult(run=state.runner.status(run_id), active=active, finished_outputs=[]),
        summary,
    )
