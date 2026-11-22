"""Running pipelines.

Stages run one at a time in spec order (references only point backwards, so that order is
always valid). For each stage the runner:

1. computes a fingerprint from the stage name and version, its parameters, and the content
   hashes of its inputs, and reuses the stored output when the checkpoint still matches;
2. otherwise runs it, retrying retryable errors when the stage is ``retry_safe``, with
   exponential backoff and jitter, and never sooner than a provider's ``retry_after_s``;
3. writes rejected items to ``rejects.jsonl`` and fails the stage above its reject share;
4. writes the outputs and then the manifest, which is the checkpoint.

Cancellation and time limits are cooperative: stages call ``ctx.check()`` between items.
A run can be resumed after a failure or cancellation; finished stages are not repeated.
"""

import logging
import platform
import random
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from importlib import metadata
from pathlib import Path

from pydantic import JsonValue

from geoproduct.engine import data as stage_data
from geoproduct.engine.data import RecordSet, StageData, item_count, kind_of
from geoproduct.engine.fingerprint import digest_files, sha256_json
from geoproduct.engine.spec import Plan, PlannedStage, compile_plan, validate_spec
from geoproduct.engine.stage import StageRef, StageRegistry, StageResult
from geoproduct.engine.store import RunStore, now
from geoproduct.errors import GeoProductError
from geoproduct.ids import new_id
from geoproduct.providers.registry import ProviderRegistry, default_providers
from geoproduct.schemas import (
    Counts,
    ErrorCode,
    GeoError,
    PipelineSpec,
    Progress,
    RetryPolicy,
    RunRecord,
    RunStatus,
    StageManifest,
    StageState,
    StageStatus,
)
from geoproduct.workspace import Workspace

log = logging.getLogger("geoproduct.engine")

FINISHED = (RunStatus.succeeded, RunStatus.failed, RunStatus.cancelled)


@dataclass(frozen=True)
class Limits:
    max_records: int = 100_000
    """Most items any stage may output."""
    max_reject_share: float = 0.1
    """Default share of input a stage may reject before it fails."""
    progress_interval_s: float = 1.0
    cancel_poll_s: float = 0.5


_ACTIVE: dict[str, threading.Event] = {}
_ACTIVE_LOCK = threading.Lock()


@contextmanager
def _claim(run_id: str) -> Iterator[threading.Event]:
    """Only one execution of a run at a time in this process."""
    with _ACTIVE_LOCK:
        if run_id in _ACTIVE:
            raise GeoProductError(
                ErrorCode.invalid_input,
                "This run is already executing.",
                details={"run_id": run_id},
                hint="Wait for it to finish, or cancel it first.",
            )
        event = threading.Event()
        _ACTIVE[run_id] = event
    try:
        yield event
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.pop(run_id, None)


def library_versions() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for dist in ("geoproduct-core", "pydantic", "shapely", "rapidfuzz", "pyyaml"):
        try:
            versions[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            continue
    return versions


class Runner:
    def __init__(
        self,
        workspace: Workspace,
        *,
        registry: StageRegistry | None = None,
        providers: ProviderRegistry | None = None,
        limits: Limits | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        from geoproduct.stages import builtin_registry

        self.workspace = workspace.ensure()
        self.registry = registry if registry is not None else builtin_registry()
        self.providers = providers if providers is not None else default_providers()
        self.limits = limits or Limits()
        self.store = RunStore(workspace)
        self.sleep = sleep
        self.random = rng or random.Random()

    def plan(self, spec: PipelineSpec) -> Plan:
        return compile_plan(spec, self.registry)

    def create(self, spec: PipelineSpec) -> RunRecord:
        """Check the spec and record a pending run, without running it."""
        plan = self.plan(spec)
        record = RunRecord(
            run_id=new_id("run"),
            pipeline_name=spec.name,
            spec_sha256=plan.spec_sha256,
            created_at=now(),
            stages=[StageState(stage_id=s.id, uses=s.definition.name) for s in plan.stages],
            versions=library_versions(),
        )
        self.store.create(record, spec)
        return record

    def run(self, spec: PipelineSpec) -> RunRecord:
        return self.execute(self.create(spec).run_id)

    def execute(self, run_id: str) -> RunRecord:
        """Run a created, failed or cancelled run to the end, reusing finished stages."""
        with _claim(run_id) as cancel_event:
            return self._execute(run_id, cancel_event)

    def resume(self, run_id: str, *, params: Mapping[str, JsonValue] | None = None) -> RunRecord:
        """Clear a cancellation, optionally change parameters, and execute again.

        Only stages whose fingerprint changed run again, with everything after them.
        """
        with _claim(run_id) as cancel_event:
            if params:
                self._change_params(run_id, params)
            self.store.clear_cancel(run_id)
            return self._execute(run_id, cancel_event)

    def cancel(self, run_id: str) -> RunRecord:
        record = self.store.load(run_id)
        if record.status in FINISHED:
            return record
        self.store.request_cancel(run_id)
        self.store.event(run_id, "cancel_requested")
        with _ACTIVE_LOCK:
            event = _ACTIVE.get(run_id)
            if event is not None:
                event.set()
                return record
            record.status = RunStatus.cancelled
            record.cancel_requested = True
            record.finished_at = now()
            self.store.save(record)
        self.store.event(run_id, "run_cancelled")
        return record

    def status(self, run_id: str) -> RunRecord:
        return self.store.load(run_id)

    def output(self, run_id: str, stage_id: str) -> StageData:
        """A finished stage's output."""
        manifest = self.store.read_manifest(run_id, stage_id)
        if manifest is None:
            raise GeoProductError(
                ErrorCode.not_found,
                f"Stage '{stage_id}' has no finished output in this run.",
                details={"run_id": run_id, "stage_id": stage_id},
            )
        plan = self.plan(self.store.load_spec(run_id))
        kind = plan.stage(stage_id).definition.output
        return stage_data.load(kind, self.store.stage_dir(run_id, stage_id))

    def _execute(self, run_id: str, cancel_event: threading.Event) -> RunRecord:
        record = self.store.load(run_id)
        if record.status == RunStatus.succeeded:
            return record
        execution = _Execution(self, record, cancel_event)
        try:
            plan = self.plan(self.store.load_spec(run_id))
        except GeoProductError as exc:
            execution.fail(None, exc.error)
            return record
        try:
            return execution.run(plan)
        except KeyboardInterrupt:
            # Stopped by the user: record it as cancelled so it can be resumed.
            running = [s for s in record.stages if s.status == StageStatus.running]
            execution.cancel(running[0] if running else None)
            raise
        except Exception as exc:
            # A failure outside any stage, such as a disk error while saving the run record.
            # Record it, so the run reads "failed" with the reason instead of "running", and
            # can be resumed. If even that cannot be saved, the original error stands.
            log.exception("run %s stopped with an unexpected error", run_id)
            running = [s for s in record.stages if s.status == StageStatus.running]
            error = GeoProductError.from_exception(exc).error
            try:
                execution.fail(running[0] if running else None, error)
            except Exception:
                raise exc from None
            return record

    def _change_params(self, run_id: str, params: Mapping[str, JsonValue]) -> None:
        spec = self.store.load_spec(run_id)
        unknown = sorted(set(params) - set(spec.params))
        if unknown:
            raise GeoProductError(
                ErrorCode.invalid_input,
                f"The spec declares no parameter(s): {', '.join(unknown)}.",
                details={"unknown": list(unknown), "declared": sorted(spec.params)},
                hint="Only parameters declared in the spec can be changed on resume.",
            )
        data = spec.model_dump(mode="json", by_alias=True)
        data["params"] = {**data["params"], **params}
        new_spec = validate_spec(data)
        plan = self.plan(new_spec)
        record = self.store.load(run_id)
        if plan.spec_sha256 == record.spec_sha256:
            return
        self.store.save_spec(run_id, new_spec)
        record.spec_sha256 = plan.spec_sha256
        if record.status == RunStatus.succeeded:
            record.status = RunStatus.pending
        self.store.save(record)
        self.store.event(run_id, "params_changed", params=sorted(params))


class _Execution:
    def __init__(self, runner: Runner, record: RunRecord, cancel_event: threading.Event) -> None:
        self.runner = runner
        self.store = runner.store
        self.limits = runner.limits
        self.record = record
        self.cancel_event = cancel_event
        self.plan: Plan | None = None
        self.cache: dict[str, StageData] = {}
        self._last_save = 0.0
        self._last_poll = 0.0

    @property
    def run_id(self) -> str:
        return self.record.run_id

    def save(self) -> None:
        self._last_save = time.monotonic()
        self.store.save(self.record)

    def event(self, name: str, **fields: object) -> None:
        self.store.event(self.run_id, name, **fields)

    def state(self, planned: PlannedStage) -> StageState:
        for state in self.record.stages:
            if state.stage_id == planned.id:
                return state
        state = StageState(stage_id=planned.id, uses=planned.definition.name)
        self.record.stages.append(state)
        return state

    def cancelled(self, *, force: bool = False) -> bool:
        if self.cancel_event.is_set():
            return True
        moment = time.monotonic()
        if force or moment - self._last_poll >= self.limits.cancel_poll_s:
            self._last_poll = moment
            if self.store.cancel_requested(self.run_id):
                self.cancel_event.set()
                return True
        return False

    def run(self, plan: Plan) -> RunRecord:
        self.plan = plan
        record = self.record
        record.status = RunStatus.running
        record.started_at = record.started_at or now()
        record.finished_at = None
        record.error = None
        record.cancel_requested = False
        self.save()
        self.event("run_started", stages=len(plan.stages))
        for planned in plan.stages:
            if self.cancelled(force=True):
                self.cancel(None)
                return record
            if not self.run_stage(planned):
                return record
        record.status = RunStatus.succeeded
        record.current_stage = None
        record.finished_at = now()
        self.save()
        self.event("run_succeeded")
        return record

    def run_stage(self, planned: PlannedStage) -> bool:
        state = self.state(planned)
        definition = planned.definition
        try:
            inputs = {stage_id: self.digest(stage_id) for stage_id in planned.dependencies}
        except GeoProductError as exc:
            return self.fail(state, exc.error)
        params_json = planned.params.model_dump(mode="json", by_alias=True)
        fingerprint = sha256_json(
            {
                "uses": definition.name,
                "version": definition.version,
                "params": params_json,
                "inputs": inputs,
            }
        )

        manifest = self.store.read_manifest(self.run_id, planned.id)
        if (
            manifest is not None
            and manifest.fingerprint == fingerprint
            and self.store.outputs_intact(manifest)
        ):
            state.status = StageStatus.skipped
            state.fingerprint = fingerprint
            state.counts = manifest.counts
            state.error = None
            state.progress = None
            self.save()
            self.event("stage_reused", stage=planned.id)
            return True

        self.store.reset_stage(self.run_id, planned.id)
        state.status = StageStatus.running
        state.started_at = now()
        state.finished_at = None
        state.error = None
        state.progress = None
        state.counts = Counts()
        self.record.current_stage = planned.id
        self.save()
        self.event("stage_started", stage=planned.id, uses=definition.name)

        try:
            data = self.load(planned.input) if planned.input else None
        except GeoProductError as exc:
            return self.fail(state, exc.error)

        policy = planned.spec.retry or RetryPolicy()
        max_attempts = policy.max_attempts if definition.retry_safe else 1
        context = _RunContext(self, planned, state)
        attempt = 0
        while True:
            attempt += 1
            state.attempts += 1
            self.save()
            context.restart_clock()
            try:
                result = definition(context, data, planned.params)
                self.check_result(planned, result)
                break
            except GeoProductError as exc:
                error = exc.error
            except Exception as exc:
                log.exception("stage %s failed unexpectedly", planned.id)
                error = GeoProductError.from_exception(exc).error
            error = error.model_copy(update={"stage": planned.id})
            if error.code == ErrorCode.cancelled:
                return self.cancel(state)
            delay = self.retry_delay(policy, attempt, error) if attempt < max_attempts else None
            if delay is None:
                return self.fail(state, error)
            self.event(
                "stage_retry",
                stage=planned.id,
                attempt=attempt,
                code=error.code.value,
                delay_s=round(delay, 3),
            )
            self.store.reset_stage(self.run_id, planned.id)
            if not self.wait(delay):
                return self.cancel(state)

        return self.finish(planned, state, fingerprint, inputs, params_json, result, data)

    def finish(
        self,
        planned: PlannedStage,
        state: StageState,
        fingerprint: str,
        inputs: dict[str, str],
        params_json: dict[str, JsonValue],
        result: StageResult,
        data: StageData | None,
    ) -> bool:
        folder = self.store.stage_dir(self.run_id, planned.id)
        rejected = len(result.rejects)
        received = result.input_count if result.input_count is not None else item_count(data)
        if result.rejects:
            self.store.write_rejects(self.run_id, planned.id, result.rejects)
        outputs = stage_data.save(result.data, folder)
        state.counts = Counts(input=received, output=item_count(result.data), rejected=rejected)

        limit = planned.spec.max_reject_share
        limit = self.limits.max_reject_share if limit is None else limit
        if received and rejected / received > limit:
            share = rejected / received
            return self.fail(
                state,
                GeoError(
                    code=ErrorCode.stage_failed,
                    message=(
                        f"Rejected {rejected} of {received} items ({share:.0%}), "
                        f"above the limit of {limit:.0%}."
                    ),
                    stage=planned.id,
                    details={"rejected": rejected, "received": received, "limit": limit},
                    hint="Read rejects.jsonl in the stage folder, fix the input, then resume; "
                    "or raise max_reject_share for this stage.",
                ),
            )

        finished_at = now()
        manifest = StageManifest(
            run_id=self.run_id,
            stage_id=planned.id,
            uses=planned.definition.name,
            stage_version=planned.definition.version,
            fingerprint=fingerprint,
            input_fingerprints=list(inputs.values()),
            params=params_json,
            outputs=outputs,
            counts=state.counts,
            providers=result.providers,
            source_ids=_source_ids(result.data),
            started_at=state.started_at or finished_at,
            finished_at=finished_at,
        )
        self.store.write_manifest(manifest)
        self.cache[planned.id] = result.data
        state.status = StageStatus.succeeded
        state.fingerprint = fingerprint
        state.finished_at = finished_at
        state.progress = None
        self.save()
        self.event(
            "stage_succeeded",
            stage=planned.id,
            received=received,
            output=state.counts.output,
            rejected=rejected,
            notes=list(result.notes),
        )
        return True

    def check_result(self, planned: PlannedStage, result: StageResult) -> None:
        expected = planned.definition.output
        actual = kind_of(result.data)
        if actual != expected:
            raise GeoProductError(
                ErrorCode.internal,
                f"Stage '{planned.id}' returned {actual or 'unknown data'}, not {expected}.",
            )
        count = item_count(result.data)
        if count > self.limits.max_records:
            raise GeoProductError(
                ErrorCode.too_large,
                f"Stage '{planned.id}' produced {count} items, more than the limit of "
                f"{self.limits.max_records}.",
                hint="Narrow the area or filter earlier in the pipeline.",
            )

    def retry_delay(self, policy: RetryPolicy, attempt: int, error: GeoError) -> float | None:
        if not error.retryable:
            return None
        base = min(policy.max_backoff_s, policy.backoff_s * 2.0 ** (attempt - 1))
        delay = base * self.runner.random.uniform(0.5, 1.0)
        retry_after = error.details.get("retry_after_s")
        if isinstance(retry_after, int | float) and not isinstance(retry_after, bool):
            if retry_after > policy.max_backoff_s:
                return None
            delay = max(delay, float(retry_after))
        return delay

    def wait(self, seconds: float) -> bool:
        """Sleep in short steps; False if the run is cancelled meanwhile."""
        remaining = seconds
        while remaining > 0:
            step = min(remaining, 0.5)
            self.runner.sleep(step)
            remaining -= step
            if self.cancelled(force=True):
                return False
        return True

    def digest(self, stage_id: str) -> str:
        manifest = self.store.read_manifest(self.run_id, stage_id)
        if manifest is None:
            raise GeoProductError(
                ErrorCode.internal,
                f"Stage '{stage_id}' has no checkpoint, so stages that use it cannot run.",
                hint="Resume the run to rebuild it.",
            )
        return digest_files(manifest.outputs)

    def load(self, stage_id: str) -> StageData:
        if stage_id not in self.cache:
            assert self.plan is not None
            kind = self.plan.stage(stage_id).definition.output
            folder = self.store.stage_dir(self.run_id, stage_id)
            self.cache[stage_id] = stage_data.load(kind, folder)
        return self.cache[stage_id]

    def fail(self, state: StageState | None, error: GeoError) -> bool:
        moment = now()
        if state is not None:
            state.status = StageStatus.failed
            state.error = error
            state.finished_at = moment
            state.progress = None
        self.record.status = RunStatus.failed
        self.record.error = error
        self.record.finished_at = moment
        self.save()
        self.event(
            "run_failed",
            stage=state.stage_id if state else None,
            code=error.code.value,
            message=error.message,
        )
        return False

    def cancel(self, state: StageState | None) -> bool:
        moment = now()
        if state is not None:
            state.status = StageStatus.cancelled
            state.finished_at = moment
            state.progress = None
        self.record.status = RunStatus.cancelled
        self.record.cancel_requested = True
        self.record.finished_at = moment
        self.save()
        self.event("run_cancelled", stage=state.stage_id if state else None)
        return False


class _RunContext:
    def __init__(self, execution: _Execution, planned: PlannedStage, state: StageState) -> None:
        self._execution = execution
        self._planned = planned
        self._state = state
        self._deadline: float | None = None
        self._started_at = now()

    def restart_clock(self) -> None:
        timeout = self._planned.spec.timeout_s
        self._deadline = time.monotonic() + timeout if timeout else None
        self._started_at = now()

    @property
    def run_id(self) -> str | None:
        return self._execution.run_id

    @property
    def stage_id(self) -> str:
        return self._planned.id

    @property
    def workspace(self) -> Workspace:
        return self._execution.runner.workspace

    @property
    def output_dir(self) -> Path:
        folder = self._execution.store.stage_dir(self._execution.run_id, self._planned.id)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    @property
    def providers(self) -> ProviderRegistry:
        return self._execution.runner.providers

    @property
    def started_at(self) -> datetime:
        return self._started_at

    def check(self) -> None:
        if self._execution.cancelled():
            raise GeoProductError(ErrorCode.cancelled, "The run was cancelled.")
        if self._deadline is not None and time.monotonic() > self._deadline:
            raise GeoProductError(
                ErrorCode.timeout,
                f"Stage '{self._planned.id}' ran longer than its "
                f"{self._planned.spec.timeout_s:g} s limit.",
                retryable=False,
                hint="Raise timeout_s for this stage, or narrow its input.",
            )

    def progress(self, done: int, total: int | None = None, message: str | None = None) -> None:
        self._state.progress = Progress(
            done=max(0, done),
            total=total if total is None else max(0, total),
            message=message[:200] if message else None,
        )
        if (
            time.monotonic() - self._execution._last_save
            >= self._execution.limits.progress_interval_s
        ):
            self._execution.save()

    def log(self, event: str, **fields: object) -> None:
        self._execution.event(event, stage=self._planned.id, **fields)

    def load(self, ref: StageRef) -> StageData:
        if ref.stage not in self._planned.refs:
            raise GeoProductError(
                ErrorCode.internal,
                f"Stage '{self._planned.id}' did not declare a reference to '{ref.stage}'.",
            )
        return self._execution.load(ref.stage)


def _source_ids(data: StageData) -> list[str]:
    if isinstance(data, RecordSet):
        return [source.id for source in data.sources]
    sources = getattr(data, "sources", None)
    return [source.id for source in sources] if sources else []
