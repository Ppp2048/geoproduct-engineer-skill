"""What every tool shares: the server state, running one stage on stored datasets, previews
and error conversion.

Tool implementations are plain synchronous functions taking a :class:`Job`, so they can be
tested without an MCP client. The server runs them in a worker thread.
"""

import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, Field, JsonValue, ValidationError

from geoproduct.engine import Runner
from geoproduct.engine.data import BuildingSet, DataKind, FileSet, RecordSet, StageData
from geoproduct.engine.stage import StageRef, StageResult, StandaloneContext
from geoproduct.errors import GeoProductError
from geoproduct.geo.shapes import anchor, record_shape
from geoproduct.providers.http import HttpSettings
from geoproduct.providers.registry import ProviderRegistry, builtin_providers
from geoproduct.schemas import Contract, ErrorCode, GeoError, QualityReport
from geoproduct.workspace import Workspace
from geoproduct_mcp.jobs import Jobs
from geoproduct_mcp.store import DatasetInfo, DatasetStore, SourceStore

log = logging.getLogger("geoproduct_mcp")

PREVIEW_ITEMS: Final = 5
CONFIRM_ABOVE_CALLS: Final = 25
"""Pipelines estimated to make more external calls than this need ``confirm: true``."""

ProgressFn = Callable[[int, int | None, str | None], None]


class ServerState:
    """Everything the tools work on. One per server process."""

    def __init__(
        self,
        workspace: Workspace,
        *,
        providers: ProviderRegistry | None = None,
        env: Mapping[str, str] | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.env = os.environ if env is None else env
        self.workspace = workspace.ensure()
        self.http = HttpSettings.from_env(self.env, self.workspace)
        self.providers = (
            providers if providers is not None else builtin_providers(self.workspace, self.env)
        )
        self.runner = runner or Runner(self.workspace, providers=self.providers)
        self.registry = self.runner.registry
        self.datasets = DatasetStore(self.workspace, self.runner)
        self.sources = SourceStore(self.workspace)
        self.jobs = Jobs(self.runner)
        self.fetch_transport: Any = None
        """Replaces the network for ``fetch_source_data`` in tests."""


@dataclass
class Job:
    """One tool call: its cancellation flag and where progress goes."""

    cancel: threading.Event = field(default_factory=threading.Event)
    progress: ProgressFn | None = None


def stop_if_cancelled(job: Job) -> None:
    """Raise if the call was cancelled. Tools call it before storing anything, because the
    worker thread of a cancelled request is abandoned, not stopped (audit GP-07)."""
    if job.cancel.is_set():
        raise GeoProductError(ErrorCode.cancelled, "The operation was cancelled.")


@dataclass
class Done:
    """A tool's result: the structured output and a one-paragraph summary for the model."""

    output: BaseModel
    summary: str


class ToolContext(StandaloneContext):
    """A stage context for a tool call: progress goes to the client, at most twice a second."""

    def __init__(self, *args: Any, report: ProgressFn | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._report = report
        self._last = 0.0

    def progress(self, done: int, total: int | None = None, message: str | None = None) -> None:
        super().progress(done, total, message)
        now = time.monotonic()
        if self._report is not None and now - self._last >= 0.5:
            self._last = now
            self._report(done, total, message)


def invalid(message: str, hint: str | None = None, **details: object) -> GeoProductError:
    return GeoProductError(ErrorCode.invalid_input, message, details=details, hint=hint)


def from_validation(exc: ValidationError, what: str) -> GeoProductError:
    """An ``invalid_input`` error listing where the input is wrong, without echoing values."""
    problems = [
        f"{'.'.join(str(p) for p in e['loc']) or what}: {e['msg']}" for e in exc.errors()[:10]
    ]
    return invalid(
        f"The {what} is not valid: {problems[0] if problems else 'see details'}.",
        hint="Read the parameter schema in geoproduct://stages and correct the listed fields.",
        problems=problems,
    )


def as_error(exc: BaseException) -> GeoError:
    if isinstance(exc, GeoProductError):
        return exc.error
    if isinstance(exc, ValidationError):
        return from_validation(exc, "input").error
    log.exception("Unexpected error in a tool", exc_info=exc)
    return GeoProductError.from_exception(exc).error


def expect_kind(state: ServerState, dataset_id: str, kinds: set[DataKind]) -> StageData:
    data = state.datasets.load(dataset_id)
    info = state.datasets.info(dataset_id)
    if info.kind not in kinds:
        wanted = " or ".join(sorted(k.value for k in kinds))
        raise invalid(
            f"Dataset '{dataset_id}' holds {info.kind.value}; this needs {wanted}.",
            dataset_id=dataset_id,
            kind=info.kind.value,
        )
    return data


def run_stage(
    state: ServerState,
    job: Job,
    stage_name: str,
    *,
    params: Mapping[str, Any],
    input_id: str | None = None,
    refs: Mapping[str, str] | None = None,
    name: str | None = None,
    created_by: str,
    adjust: Callable[[StageData], StageData] | None = None,
) -> tuple[DatasetInfo, StageData, StageResult]:
    """Run one registered stage on stored datasets and store its output as a new dataset.

    ``refs`` maps the alias used in a ``{"stage": alias}`` parameter to a dataset id.
    """
    definition = state.registry.get(stage_name)
    data: StageData | None = None
    inputs: list[str] = []
    if definition.input is not None:
        if input_id is None:
            raise invalid(f"Stage {stage_name} needs an input dataset.")
        data = expect_kind(state, input_id, {definition.input})
        inputs.append(input_id)
    loaded: dict[str, StageData] = {}
    for alias, dataset_id in (refs or {}).items():
        loaded[alias] = expect_kind(state, dataset_id, set(definition.ref_kinds))
        inputs.append(dataset_id)
    try:
        parsed = definition.params.model_validate(dict(params))
    except ValidationError as exc:
        raise from_validation(exc, f"parameters for {stage_name}") from None
    dataset_id, folder = state.datasets.reserve()
    try:
        ctx = ToolContext(
            state.workspace,
            folder,
            providers=state.providers,
            refs=loaded,
            stage_id=created_by.replace(".", "_")[:64],
            cancel=job.cancel,
            report=job.progress,
        )
        result = definition(ctx, data, parsed)
        output = adjust(result.data) if adjust else result.data
        # A cancelled call can still be running here (its thread is abandoned): keep nothing.
        stop_if_cancelled(job)
        info = state.datasets.commit(
            dataset_id,
            output,
            name=name,
            created_by=created_by,
            inputs=inputs,
            notes=result.notes,
            rejects=result.rejects,
        )
    except BaseException:
        state.datasets.discard(dataset_id)
        raise
    return info, output, result


def ref(alias: str) -> dict[str, str]:
    return StageRef(stage=alias).model_dump()


def compact_record(record: Any) -> dict[str, JsonValue]:
    item: dict[str, JsonValue] = {"id": record.id}
    for key in ("name", "category"):
        if (value := getattr(record, key)) is not None:
            item[key] = value
    item["status"] = record.status.value
    shape = record_shape(record)
    if shape is not None and not shape.is_empty:
        lon, lat = anchor(shape)
        item["position"] = [round(lon, 6), round(lat, 6)]
        item["location_kind"] = record.location.kind.value
    if record.confidence is not None:
        item["confidence"] = record.confidence.level.value
    if record.building_match is not None:
        item["building_match"] = record.building_match.status.value
    if record.conflicts:
        item["conflicts"] = [c.field for c in record.conflicts]
    if record.limitations:
        item["limitations"] = record.limitations[:2]
    return item


def preview(data: StageData, limit: int = PREVIEW_ITEMS) -> list[dict[str, JsonValue]]:
    """A few compact items, so the model sees what a dataset holds without loading it."""
    match data:
        case RecordSet():
            return [compact_record(r) for r in data.records[:limit]]
        case BuildingSet():
            return [
                {
                    "ref": f"{b.ref.provider}:{b.ref.id}",
                    "name": b.name,
                    "levels": b.levels,
                    "height_m": b.height_m,
                }
                for b in data.buildings[:limit]
            ]
        case QualityReport():
            return [f.model_dump(mode="json", exclude_none=True) for f in data.findings[:limit]]
        case FileSet():
            return [f.model_dump(mode="json", exclude_none=True) for f in data.files[:limit]]
    return []


def licence_lines(data: StageData) -> list[str]:
    """Attribution lines required by the licences of the data's sources."""
    sources = getattr(data, "sources", [])
    lines = [s.attribution for s in sources if s.attribution]
    return list(dict.fromkeys(lines))[:10]


class DatasetResult(Contract):
    dataset: DatasetInfo
    preview: list[dict[str, JsonValue]]
    """The first few items, in compact form."""
    attribution: list[str] = Field(default_factory=list)
    """Credit lines the data's licences require; show them wherever the data is used."""


def dataset_result(info: DatasetInfo, data: StageData) -> DatasetResult:
    return DatasetResult(dataset=info, preview=preview(data), attribution=licence_lines(data))


def describe(info: DatasetInfo) -> str:
    noun = {"records": "record", "buildings": "building", "report": "finding", "files": "file"}
    text = f"Dataset {info.dataset_id}: {info.item_count} {noun[info.kind.value]}(s)"
    if info.rejected:
        text += f", {info.rejected} rejected (see rejects.jsonl in {info.folder})"
    notes = "; ".join(info.notes[:3])
    return f"{text}.{f' {notes}.' if notes else ''}"


def workspace_path(state: ServerState, path: Path) -> str:
    return path.relative_to(state.workspace.root).as_posix()
