"""The stage abstraction: a registered, typed function from input data and parameters to output.

A stage is declared with :func:`stage`::

    @stage(name="normalize.records", params=NormalizeParams, input=DataKind.records,
           output=DataKind.records, summary="Tidy names, categories and coordinates.")
    def normalize_records(ctx: StageContext, data: RecordSet, params: NormalizeParams):
        ...
        return StageResult(RecordSet(...))

Specs can only name registered stages, so a pipeline is always a composition of reviewed
code, never code written by a model.
"""

import difflib
import re
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, Field

from geoproduct.engine.data import DataKind, StageData
from geoproduct.errors import GeoProductError
from geoproduct.providers.registry import ProviderRegistry
from geoproduct.schemas import Contract, ErrorCode, ProviderUse
from geoproduct.schemas._base import ShortText, Slug, Text
from geoproduct.workspace import Workspace

_STAGE_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class StageRef(Contract):
    """``{"stage": "<id>"}`` in ``with``: the output of an earlier stage used as a parameter."""

    stage: Slug


class Reject(Contract):
    """An input item a stage could not use, and why. Written to ``rejects.jsonl``."""

    reason: Text
    index: Annotated[int, Field(ge=0)] | None = None
    record_id: ShortText | None = None


@dataclass
class StageResult:
    data: StageData
    rejects: list[Reject] = field(default_factory=list)
    input_count: int | None = None
    """Items received; defaults to the size of the input data."""
    providers: list[ProviderUse] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class StageContext(Protocol):
    """What a stage may use while it runs."""

    @property
    def run_id(self) -> str | None: ...
    @property
    def stage_id(self) -> str: ...
    @property
    def workspace(self) -> Workspace: ...
    @property
    def output_dir(self) -> Path: ...
    @property
    def providers(self) -> ProviderRegistry: ...
    @property
    def started_at(self) -> datetime: ...

    def check(self) -> None:
        """Raise if the run was cancelled or the stage is out of time. Call between items."""

    def progress(self, done: int, total: int | None = None, message: str | None = None) -> None:
        """Report progress; cheap to call often."""

    def log(self, event: str, **fields: object) -> None:
        """Add an entry to the run's event log."""

    def load(self, ref: StageRef) -> StageData:
        """The output of a stage referenced in this stage's parameters."""


@dataclass(frozen=True)
class StageDef[P: BaseModel]:
    name: str
    version: str
    summary: str
    params: type[P]
    input: DataKind | None
    output: DataKind
    func: Callable[[StageContext, Any, P], StageResult]
    ref_kinds: frozenset[DataKind] = frozenset()
    """Kinds of data this stage accepts through ``{"stage": ...}`` parameters."""
    retry_safe: bool = True
    """Running it again after a failure has no side effects beyond its own folder."""

    def __call__(self, ctx: StageContext, data: Any, params: P) -> StageResult:
        return self.func(ctx, data, params)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "summary": self.summary,
            "input": self.input.value if self.input else None,
            "output": self.output.value,
            "references": sorted(k.value for k in self.ref_kinds),
            "retry_safe": self.retry_safe,
            "params": self.params.model_json_schema(by_alias=True),
        }


def stage[P: BaseModel](
    *,
    name: str,
    params: type[P],
    input: DataKind | None,
    output: DataKind,
    summary: str,
    version: str = "1",
    ref_kinds: frozenset[DataKind] = frozenset(),
    retry_safe: bool = True,
) -> Callable[[Callable[[StageContext, Any, P], StageResult]], StageDef[P]]:
    if not _STAGE_NAME.match(name):
        raise ValueError(f"invalid stage name: {name}")

    def register(func: Callable[[StageContext, Any, P], StageResult]) -> StageDef[P]:
        return StageDef(
            name=name,
            version=version,
            summary=summary,
            params=params,
            input=input,
            output=output,
            func=func,
            ref_kinds=ref_kinds,
            retry_safe=retry_safe,
        )

    return register


class StageRegistry:
    def __init__(self, stages: Mapping[str, StageDef[Any]] | None = None) -> None:
        self._stages: dict[str, StageDef[Any]] = dict(stages or {})

    def add(self, definition: StageDef[Any]) -> None:
        if definition.name in self._stages:
            raise ValueError(f"stage already registered: {definition.name}")
        self._stages[definition.name] = definition

    def get(self, name: str) -> StageDef[Any]:
        try:
            return self._stages[name]
        except KeyError:
            close = difflib.get_close_matches(name, self._stages, n=3)
            raise GeoProductError(
                ErrorCode.unknown_stage,
                f"No registered stage is called '{name}'.",
                details={"stage": name, "similar": list(close)},
                hint="List the registered stages and use one of their names.",
            ) from None

    def __contains__(self, name: object) -> bool:
        return name in self._stages

    def __iter__(self) -> Iterator[StageDef[Any]]:
        return iter(sorted(self._stages.values(), key=lambda d: d.name))

    def names(self) -> list[str]:
        return sorted(self._stages)


class StandaloneContext:
    """A context for running one stage outside a pipeline, e.g. from a tool call or a test.

    ``refs`` maps stage ids to the data a ``{"stage": ...}`` parameter should receive.
    """

    def __init__(
        self,
        workspace: Workspace,
        output_dir: Path,
        *,
        providers: ProviderRegistry | None = None,
        refs: Mapping[str, StageData] | None = None,
        stage_id: str = "standalone",
        cancel: threading.Event | None = None,
    ) -> None:
        self._workspace = workspace
        self._output_dir = output_dir
        self._providers = providers if providers is not None else ProviderRegistry()
        self._refs = dict(refs or {})
        self._stage_id = stage_id
        self._cancel = cancel or threading.Event()
        self._started_at = datetime.now(UTC)
        self.events: list[dict[str, object]] = []
        self.last_progress: tuple[int, int | None, str | None] | None = None

    @property
    def run_id(self) -> str | None:
        return None

    @property
    def stage_id(self) -> str:
        return self._stage_id

    @property
    def workspace(self) -> Workspace:
        return self._workspace

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    @property
    def providers(self) -> ProviderRegistry:
        return self._providers

    @property
    def started_at(self) -> datetime:
        return self._started_at

    def check(self) -> None:
        if self._cancel.is_set():
            raise GeoProductError(ErrorCode.cancelled, "The operation was cancelled.")

    def progress(self, done: int, total: int | None = None, message: str | None = None) -> None:
        self.last_progress = (done, total, message)

    def log(self, event: str, **fields: object) -> None:
        self.events.append({"event": event, **fields})

    def load(self, ref: StageRef) -> StageData:
        try:
            return self._refs[ref.stage]
        except KeyError:
            raise GeoProductError(
                ErrorCode.not_found, f"No data was supplied for stage '{ref.stage}'."
            ) from None
