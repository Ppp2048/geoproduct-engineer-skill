"""The pipeline engine, driven by small test stages: retries, checkpoints, resume,
cancellation, timeouts, limits and spec checking."""

import json
import random
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from geoproduct.engine import (
    DataKind,
    Limits,
    RecordSet,
    Runner,
    StageContext,
    StageDef,
    StageRef,
    StageRegistry,
    StageResult,
    compile_plan,
    parse_spec,
    stage,
    validate_spec,
)
from geoproduct.engine.stage import Reject
from geoproduct.errors import GeoProductError
from geoproduct.providers.registry import ProviderRegistry
from geoproduct.schemas import (
    Contract,
    ErrorCode,
    PipelineSpec,
    QualityReport,
    Record,
    RunStatus,
    StageStatus,
)
from geoproduct.workspace import Workspace

# Test stages --------------------------------------------------------------------------------


class MakeParams(Contract):
    count: int = 3


class TagParams(Contract):
    tag: str = ""


class RefParams(Contract):
    other: StageRef


Behaviour = Callable[[StageContext, Any, Any], StageResult]


def records(count: int) -> RecordSet:
    return RecordSet(records=[Record(id=f"r-{i}", name=f"Item {i}") for i in range(count)])


def make_stage(calls: list[str] | None = None) -> StageDef[MakeParams]:
    def make(ctx: StageContext, data: None, params: MakeParams) -> StageResult:
        if calls is not None:
            calls.append("make")
        return StageResult(records(params.count))

    return stage(
        name="test.make", params=MakeParams, input=None, output=DataKind.records, summary="Make."
    )(make)


def records_stage(
    name: str, behaviour: Behaviour, *, retry_safe: bool = True
) -> StageDef[TagParams]:
    return stage(
        name=name,
        params=TagParams,
        input=DataKind.records,
        output=DataKind.records,
        summary="Test stage.",
        retry_safe=retry_safe,
    )(behaviour)


def passthrough(calls: list[str] | None = None) -> StageDef[TagParams]:
    def run(ctx: StageContext, data: RecordSet, params: TagParams) -> StageResult:
        if calls is not None:
            calls.append(f"pass:{params.tag}")
        return StageResult(data)

    return records_stage("test.pass", run)


def failing(codes: list[ErrorCode | None], **details: Any) -> Behaviour:
    """Fail with each code in turn (None means succeed), then succeed."""
    queue = list(codes)

    def run(ctx: StageContext, data: RecordSet, params: TagParams) -> StageResult:
        code = queue.pop(0) if queue else None
        if code is not None:
            raise GeoProductError(code, f"test failure: {code}", details=details)
        return StageResult(data)

    return run


class Harness:
    def __init__(self, root: Path, *defs: StageDef[Any], limits: Limits | None = None) -> None:
        self.sleeps: list[float] = []
        self.registry = StageRegistry({d.name: d for d in defs})
        self.runner = Runner(
            Workspace(root),
            registry=self.registry,
            providers=ProviderRegistry(),
            limits=limits,
            sleep=self.sleeps.append,
            rng=random.Random(7),
        )

    def run(self, stages: list[dict[str, Any]], params: dict[str, Any] | None = None) -> Any:
        return self.runner.run(spec(stages, params))

    def state(self, record: Any, stage_id: str) -> Any:
        return next(s for s in record.stages if s.stage_id == stage_id)

    def events(self, run_id: str) -> list[dict[str, Any]]:
        return self.runner.store.events(run_id)


def spec(stages: list[dict[str, Any]], params: dict[str, Any] | None = None) -> PipelineSpec:
    return validate_spec({"name": "test", "params": params or {}, "stages": stages})


MAKE = {"id": "a", "uses": "test.make"}


# Running and checkpoints --------------------------------------------------------------------


def test_run_writes_outputs_manifests_and_events(tmp_path: Path) -> None:
    h = Harness(tmp_path, make_stage(), passthrough())
    record = h.run([MAKE, {"id": "b", "uses": "test.pass", "input": "a"}])

    assert record.status == RunStatus.succeeded
    assert [s.status for s in record.stages] == [StageStatus.succeeded] * 2
    assert h.state(record, "b").counts.output == 3
    folder = h.runner.store.stage_dir(record.run_id, "b")
    assert {p.name for p in folder.iterdir()} == {
        "manifest.json",
        "records.geojson",
        "provenance.json",
    }
    output = h.runner.output(record.run_id, "b")
    assert isinstance(output, RecordSet)
    assert [r.id for r in output.records] == ["r-0", "r-1", "r-2"]
    kinds = [e["event"] for e in h.events(record.run_id)]
    assert kinds[:2] == ["run_created", "run_started"]
    assert kinds[-1] == "run_succeeded"
    assert h.runner.status(record.run_id).status == RunStatus.succeeded


def test_a_torn_event_line_is_skipped_and_ended(tmp_path: Path) -> None:
    """Audit GP-04: a line torn by a process killed mid-write made every read of the log
    fail, and the next event was appended onto it. The torn line now cuts a multi-byte
    character in half, as a real one can."""
    h = Harness(tmp_path, make_stage())
    record = h.run([MAKE])
    log = h.runner.store.run_dir(record.run_id) / "events.jsonl"
    with log.open("ab") as handle:
        handle.write('{"ts":"2026-01-01","event":"stage_retry","note":"café'.encode()[:-1])
    kinds = [e["event"] for e in h.events(record.run_id)]
    assert kinds[-1] == "run_succeeded"

    h.runner.store.event(record.run_id, "after_the_tear", n=1)
    events = h.events(record.run_id)
    assert [e["event"] for e in events] == [*kinds, "after_the_tear"]
    assert events[-1]["n"] == 1
    assert log.read_bytes().endswith(b"\n")


def test_failed_run_resumes_from_checkpoints(tmp_path: Path) -> None:
    calls: list[str] = []
    flaky = records_stage("test.flaky", failing([ErrorCode.invalid_input]))
    h = Harness(tmp_path, make_stage(calls), flaky, passthrough(calls))
    stages = [
        MAKE,
        {"id": "b", "uses": "test.flaky", "input": "a"},
        {"id": "c", "uses": "test.pass", "input": "b"},
    ]
    first = h.run(stages)
    assert first.status == RunStatus.failed
    assert first.error is not None
    assert first.error.stage == "b"
    assert h.state(first, "c").status == StageStatus.pending

    resumed = h.runner.resume(first.run_id)
    assert resumed.status == RunStatus.succeeded
    assert [h.state(resumed, s).status for s in "abc"] == [
        StageStatus.skipped,
        StageStatus.succeeded,
        StageStatus.succeeded,
    ]
    assert calls == ["make", "pass:"]


def test_resume_with_changed_parameter_reruns_only_affected_stages(tmp_path: Path) -> None:
    calls: list[str] = []
    h = Harness(tmp_path, make_stage(calls), passthrough(calls))
    stages = [
        {"id": "a", "uses": "test.make", "with": {"count": {"param": "count"}}},
        {"id": "b", "uses": "test.pass", "input": "a", "with": {"tag": {"param": "tag"}}},
    ]
    record = h.run(stages, {"count": 2, "tag": "x"})
    assert record.status == RunStatus.succeeded

    record = h.runner.resume(record.run_id, params={"tag": "y"})
    assert record.status == RunStatus.succeeded
    assert h.state(record, "a").status == StageStatus.skipped
    assert h.state(record, "b").status == StageStatus.succeeded
    assert calls == ["make", "pass:x", "pass:y"]

    record = h.runner.resume(record.run_id, params={"count": 4})
    assert h.state(record, "b").counts.output == 4
    assert calls[-2:] == ["make", "pass:y"]

    with pytest.raises(GeoProductError) as caught:
        h.runner.resume(record.run_id, params={"nope": 1})
    assert caught.value.code == ErrorCode.invalid_input


def test_changed_stage_output_invalidates_its_checkpoint(tmp_path: Path) -> None:
    calls: list[str] = []
    flaky = records_stage("test.flaky", failing([ErrorCode.invalid_input]))
    h = Harness(tmp_path, make_stage(calls), flaky)
    record = h.run([MAKE, {"id": "b", "uses": "test.flaky", "input": "a"}])
    output = h.runner.store.stage_dir(record.run_id, "a") / "records.geojson"
    output.write_text('{"type": "FeatureCollection", "features": []}', encoding="utf-8")

    record = h.runner.resume(record.run_id)
    assert record.status == RunStatus.succeeded
    assert h.state(record, "a").status == StageStatus.succeeded
    assert calls == ["make", "make"]


# Retries ------------------------------------------------------------------------------------


def test_retryable_errors_are_retried_with_backoff(tmp_path: Path) -> None:
    flaky = records_stage(
        "test.flaky", failing([ErrorCode.provider_unavailable, ErrorCode.provider_unavailable])
    )
    h = Harness(tmp_path, make_stage(), flaky)
    record = h.run([MAKE, {"id": "b", "uses": "test.flaky", "input": "a"}])

    assert record.status == RunStatus.succeeded
    assert h.state(record, "b").attempts == 3
    # Backoff 1 s then 2 s, each scaled by jitter between 0.5 and 1.
    assert 1.5 <= sum(h.sleeps) <= 3.0
    retries = [e for e in h.events(record.run_id) if e["event"] == "stage_retry"]
    assert [e["attempt"] for e in retries] == [1, 2]


def test_retry_after_from_the_provider_is_honoured(tmp_path: Path) -> None:
    flaky = records_stage("test.flaky", failing([ErrorCode.rate_limited], retry_after_s=4))
    h = Harness(tmp_path, make_stage(), flaky)
    record = h.run([MAKE, {"id": "b", "uses": "test.flaky", "input": "a"}])
    assert record.status == RunStatus.succeeded
    assert sum(h.sleeps) == pytest.approx(4.0)


def test_retry_after_beyond_the_backoff_limit_fails_without_waiting(tmp_path: Path) -> None:
    flaky = records_stage("test.flaky", failing([ErrorCode.rate_limited], retry_after_s=999))
    h = Harness(tmp_path, make_stage(), flaky)
    record = h.run([MAKE, {"id": "b", "uses": "test.flaky", "input": "a"}])
    assert record.status == RunStatus.failed
    assert record.error is not None
    assert record.error.code == ErrorCode.rate_limited
    assert h.sleeps == []


def test_retries_stop_at_max_attempts(tmp_path: Path) -> None:
    flaky = records_stage("test.flaky", failing([ErrorCode.provider_unavailable] * 5))
    h = Harness(tmp_path, make_stage(), flaky)
    stages = [
        MAKE,
        {"id": "b", "uses": "test.flaky", "input": "a", "retry": {"max_attempts": 2}},
    ]
    record = h.run(stages)
    assert record.status == RunStatus.failed
    assert h.state(record, "b").attempts == 2


@pytest.mark.parametrize("retry_safe", [True, False])
def test_errors_are_retried_only_when_retryable_and_safe(tmp_path: Path, retry_safe: bool) -> None:
    code = ErrorCode.provider_unavailable if not retry_safe else ErrorCode.invalid_input
    flaky = records_stage("test.flaky", failing([code]), retry_safe=retry_safe)
    h = Harness(tmp_path, make_stage(), flaky)
    record = h.run([MAKE, {"id": "b", "uses": "test.flaky", "input": "a"}])
    assert record.status == RunStatus.failed
    assert h.state(record, "b").attempts == 1


def test_unexpected_exceptions_do_not_leak_their_arguments(tmp_path: Path) -> None:
    def explode(ctx: StageContext, data: RecordSet, params: TagParams) -> StageResult:
        raise ValueError("token=s3cr3t-value")

    h = Harness(tmp_path, make_stage(), records_stage("test.explode", explode))
    record = h.run([MAKE, {"id": "b", "uses": "test.explode", "input": "a"}])
    assert record.status == RunStatus.failed
    assert record.error is not None
    assert record.error.code == ErrorCode.internal
    run_dir = h.runner.store.run_dir(record.run_id)
    for name in ("run.json", "events.jsonl"):
        assert "s3cr3t" not in (run_dir / name).read_text(encoding="utf-8")


# Limits -------------------------------------------------------------------------------------


def rejecting(share: int) -> Behaviour:
    def run(ctx: StageContext, data: RecordSet, params: TagParams) -> StageResult:
        rejects = [Reject(index=i, reason="test reject") for i in range(share)]
        return StageResult(data, rejects=rejects, input_count=len(data.records))

    return run


def test_too_many_rejects_fail_the_stage(tmp_path: Path) -> None:
    h = Harness(tmp_path, make_stage(), records_stage("test.reject", rejecting(1)))
    record = h.run([MAKE, {"id": "b", "uses": "test.reject", "input": "a"}])
    assert record.status == RunStatus.failed
    assert record.error is not None
    assert record.error.code == ErrorCode.stage_failed
    rejects = h.runner.store.stage_dir(record.run_id, "b") / "rejects.jsonl"
    assert json.loads(rejects.read_text(encoding="utf-8").splitlines()[0])["reason"]


def test_reject_share_can_be_raised_per_stage(tmp_path: Path) -> None:
    h = Harness(tmp_path, make_stage(), records_stage("test.reject", rejecting(1)))
    stages = [MAKE, {"id": "b", "uses": "test.reject", "input": "a", "max_reject_share": 0.5}]
    record = h.run(stages)
    assert record.status == RunStatus.succeeded
    assert h.state(record, "b").counts.rejected == 1


def test_output_size_limit(tmp_path: Path) -> None:
    h = Harness(tmp_path, make_stage(), limits=Limits(max_records=2))
    record = h.run([MAKE])
    assert record.error is not None
    assert record.error.code == ErrorCode.too_large


def test_a_stage_returning_the_wrong_kind_fails(tmp_path: Path) -> None:
    def wrong(ctx: StageContext, data: RecordSet, params: TagParams) -> StageResult:
        return StageResult(QualityReport.model_validate(_empty_report()))

    h = Harness(tmp_path, make_stage(), records_stage("test.wrong", wrong))
    record = h.run([MAKE, {"id": "b", "uses": "test.wrong", "input": "a"}])
    assert record.error is not None
    assert record.error.code == ErrorCode.internal


def _empty_report() -> dict[str, Any]:
    return {
        "dataset": "x",
        "checks": [],
        "score": 1,
        "metrics": {
            "record_count": 0,
            "with_geometry": 0,
            "missing_geometry": 0,
            "records_with_errors": 0,
            "records_with_warnings_only": 0,
            "error_share": 0,
            "unresolved_share": 0,
            "conflicting_share": 0,
        },
    }


# Cancellation, timeouts and interruption ---------------------------------------------------


def test_cancel_stops_a_running_stage_and_the_run_can_resume(tmp_path: Path) -> None:
    seen: list[int] = []
    h: Harness

    def slow(ctx: StageContext, data: RecordSet, params: TagParams) -> StageResult:
        for i in range(50):
            if i == 5 and not seen:
                h.runner.cancel(str(ctx.run_id))
            if i >= 5:
                seen.append(i)
            ctx.check()
        return StageResult(data)

    h = Harness(tmp_path, make_stage(), records_stage("test.slow", slow))
    record = h.run([MAKE, {"id": "b", "uses": "test.slow", "input": "a"}])
    assert record.status == RunStatus.cancelled
    assert h.state(record, "b").status == StageStatus.cancelled
    assert seen == [5]

    record = h.runner.resume(record.run_id)
    assert record.status == RunStatus.succeeded
    assert h.state(record, "a").status == StageStatus.skipped


def test_cancel_requested_through_the_store_is_noticed(tmp_path: Path) -> None:
    """Another process cancels by writing a marker file the running stage polls."""
    h: Harness

    def slow(ctx: StageContext, data: RecordSet, params: TagParams) -> StageResult:
        h.runner.store.request_cancel(str(ctx.run_id))
        for _ in range(100):
            ctx.check()
        return StageResult(data)

    h = Harness(
        tmp_path,
        make_stage(),
        records_stage("test.slow", slow),
        limits=Limits(cancel_poll_s=0.0),
    )
    record = h.run([MAKE, {"id": "b", "uses": "test.slow", "input": "a"}])
    assert record.status == RunStatus.cancelled


def test_cancelling_a_finished_run_changes_nothing(tmp_path: Path) -> None:
    h = Harness(tmp_path, make_stage())
    record = h.run([MAKE])
    assert h.runner.cancel(record.run_id).status == RunStatus.succeeded


def test_stage_timeout_fails_without_retrying(tmp_path: Path) -> None:
    def slow(ctx: StageContext, data: RecordSet, params: TagParams) -> StageResult:
        while True:
            time.sleep(0.005)
            ctx.check()

    h = Harness(tmp_path, make_stage(), records_stage("test.slow", slow))
    record = h.run([MAKE, {"id": "b", "uses": "test.slow", "input": "a", "timeout_s": 0.05}])
    assert record.status == RunStatus.failed
    assert record.error is not None
    assert record.error.code == ErrorCode.timeout
    assert h.state(record, "b").attempts == 1


def test_a_run_cannot_execute_twice_at_once(tmp_path: Path) -> None:
    refused: list[ErrorCode] = []
    h: Harness

    def reenter(ctx: StageContext, data: RecordSet, params: TagParams) -> StageResult:
        try:
            h.runner.execute(str(ctx.run_id))
        except GeoProductError as exc:
            refused.append(exc.code)
        return StageResult(data)

    h = Harness(tmp_path, make_stage(), records_stage("test.reenter", reenter))
    record = h.run([MAKE, {"id": "b", "uses": "test.reenter", "input": "a"}])
    assert record.status == RunStatus.succeeded
    assert refused == [ErrorCode.invalid_input]


def test_keyboard_interrupt_leaves_a_resumable_cancelled_run(tmp_path: Path) -> None:
    interrupts = [True]

    def interrupted(ctx: StageContext, data: RecordSet, params: TagParams) -> StageResult:
        if interrupts:
            interrupts.pop()
            raise KeyboardInterrupt
        return StageResult(data)

    h = Harness(tmp_path, make_stage(), records_stage("test.interrupt", interrupted))
    created = h.runner.create(spec([MAKE, {"id": "b", "uses": "test.interrupt", "input": "a"}]))
    with pytest.raises(KeyboardInterrupt):
        h.runner.execute(created.run_id)
    stored = h.runner.status(created.run_id)
    assert stored.status == RunStatus.cancelled
    assert h.state(stored, "b").status == StageStatus.cancelled
    assert h.runner.resume(created.run_id).status == RunStatus.succeeded


def test_an_error_outside_any_stage_is_recorded_and_the_run_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disk error while saving the run record must not leave the run reading "running"."""
    h = Harness(tmp_path, make_stage(), passthrough())
    created = h.runner.create(spec([MAKE, {"id": "b", "uses": "test.pass", "input": "a"}]))
    save = h.runner.store.save
    failures = [OSError("disk unavailable")]

    def flaky_save(record: Any) -> None:
        if failures and any(s.status == StageStatus.succeeded for s in record.stages):
            raise failures.pop()
        save(record)

    monkeypatch.setattr(h.runner.store, "save", flaky_save)
    failed = h.runner.execute(created.run_id)
    assert failed.status == RunStatus.failed
    assert failed.error is not None
    assert "disk unavailable" not in failed.error.model_dump_json(), "no exception text leaks"
    assert h.runner.status(created.run_id).status == RunStatus.failed
    assert h.runner.resume(created.run_id).status == RunStatus.succeeded


# Specs and plans ----------------------------------------------------------------------------


def ref_stage() -> StageDef[RefParams]:
    def run(ctx: StageContext, data: RecordSet, params: RefParams) -> StageResult:
        other = ctx.load(params.other)
        assert isinstance(other, RecordSet)
        return StageResult(data.model_copy(update={"records": data.records + other.records}))

    return stage(
        name="test.refs",
        params=RefParams,
        input=DataKind.records,
        output=DataKind.records,
        ref_kinds=frozenset({DataKind.records}),
        summary="Uses a reference.",
    )(run)


def test_stage_references_become_dependencies(tmp_path: Path) -> None:
    h = Harness(tmp_path, make_stage(), ref_stage())
    stages = [
        MAKE,
        {"id": "b", "uses": "test.make", "with": {"count": 2}},
        {"id": "c", "uses": "test.refs", "input": "a", "with": {"other": {"stage": "b"}}},
    ]
    plan = h.runner.plan(spec(stages))
    assert plan.stage("c").dependencies == ("a", "b")
    record = h.runner.run(spec(stages))
    assert h.state(record, "c").counts.output == 5


def test_compile_reports_every_problem_at_once(tmp_path: Path) -> None:
    h = Harness(tmp_path, make_stage(), passthrough(), ref_stage())
    bad = spec(
        [
            {"id": "a", "uses": "test.mak"},
            {"id": "b", "uses": "test.make", "with": {"count": "many"}},
            {"id": "c", "uses": "test.pass"},
            {"id": "d", "uses": "test.make", "input": "b"},
            {"id": "e", "uses": "test.make"},
            {"id": "f", "uses": "test.refs", "input": "e", "with": {"other": {"stage": "e"}}},
        ]
    )
    with pytest.raises(GeoProductError) as caught:
        compile_plan(bad, h.registry)
    error = caught.value.error
    assert error.code == ErrorCode.invalid_spec
    problems = "\n".join(error.details["problems"])  # type: ignore[arg-type]
    assert "no registered stage 'test.mak' (did you mean test.make" in problems
    assert "with.count" in problems
    assert "test.pass needs an input of records" in problems
    assert "test.make takes no input" in problems


def test_reference_to_a_report_is_refused_where_records_are_needed(tmp_path: Path) -> None:
    def report(ctx: StageContext, data: None, params: MakeParams) -> StageResult:
        return StageResult(QualityReport.model_validate(_empty_report()))

    report_stage = stage(
        name="test.report", params=MakeParams, input=None, output=DataKind.report, summary="R."
    )(report)
    h = Harness(tmp_path, make_stage(), ref_stage(), report_stage)
    bad = spec(
        [
            MAKE,
            {"id": "r", "uses": "test.report"},
            {"id": "c", "uses": "test.refs", "input": "a", "with": {"other": {"stage": "r"}}},
        ]
    )
    with pytest.raises(GeoProductError) as caught:
        compile_plan(bad, h.registry)
    assert "'r' produces report" in str(caught.value.error.details["problems"])


def test_specs_are_parsed_safely() -> None:
    yaml_spec = "name: demo\nstages:\n  - {id: a, uses: test.make}\n"
    assert parse_spec(yaml_spec).name == "demo"
    assert parse_spec(json.dumps({"name": "demo", "stages": [MAKE]})).stages[0].id == "a"
    for text in ("!!python/object/apply:os.system ['echo hi']", "- just\n- a list\n", "{"):
        with pytest.raises(GeoProductError) as caught:
            parse_spec(text)
        assert caught.value.code == ErrorCode.invalid_spec
    with pytest.raises(GeoProductError) as caught:
        parse_spec("name: x\n" + "#" * 1_000_001)
    assert caught.value.code == ErrorCode.too_large


def test_spec_level_problems_are_listed(tmp_path: Path) -> None:
    with pytest.raises(GeoProductError) as caught:
        validate_spec(
            {
                "name": "demo",
                "stages": [
                    {"id": "a", "uses": "test.make", "with": {"count": {"param": "missing"}}},
                    {"id": "a", "uses": "test.pass", "input": "later"},
                ],
            }
        )
    problems = str(caught.value.error.details["problems"])
    assert "undefined parameter 'missing'" in problems
    assert "used more than once" in problems
    assert "'later', which is not defined before it" in problems
