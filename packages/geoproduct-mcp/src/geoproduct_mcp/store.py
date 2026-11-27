"""Handles kept in the workspace: datasets and sources.

Tools pass data around by id instead of through the model's context. A dataset is a stored
stage output: records, building footprints, a quality report or a set of exported files.
It is either

- created by a tool, in ``datasets/<dataset_id>/`` (ids look like ``ds_01k...``), or
- the output of a pipeline stage, addressed as ``<run_id>/<stage_id>`` and read in place.

Sources are the agent's citations, stored in ``sources/<source_id>/source.json`` with
anything fetched from them beside it.
"""

import re
import shutil
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated, Final

from pydantic import AwareDatetime, Field

from geoproduct.engine import Runner
from geoproduct.engine import data as stage_data
from geoproduct.engine.data import DataKind, StageData, item_count, kind_of
from geoproduct.engine.stage import Reject
from geoproduct.engine.store import now
from geoproduct.errors import GeoProductError
from geoproduct.ids import new_id
from geoproduct.schemas import Contract, ErrorCode, FileRef, Source
from geoproduct.schemas._base import Identifier, ShortText, Text
from geoproduct.workspace import Workspace

DATASET_ID: Final = re.compile(r"^ds_[0-9a-z]{26}$")
RUN_OUTPUT_ID: Final = re.compile(r"^(run_[0-9a-z]{26})/([a-z][a-z0-9_-]{0,63})$")
SOURCE_ID: Final = re.compile(r"^src_[0-9a-z]{8,40}$")


class DatasetInfo(Contract):
    dataset_id: Identifier
    kind: DataKind
    name: ShortText | None = None
    item_count: Annotated[int, Field(ge=0)]
    rejected: Annotated[int, Field(ge=0)] = 0
    created_at: AwareDatetime
    created_by: ShortText
    """The tool, or the pipeline run and stage, that produced it."""
    inputs: list[Identifier] = Field(default_factory=list)
    notes: list[Text] = Field(default_factory=list)
    folder: ShortText
    """Where its files are, relative to the workspace."""
    files: list[FileRef] = Field(default_factory=list)


def _unknown_dataset(dataset_id: str) -> GeoProductError:
    return GeoProductError(
        ErrorCode.not_found,
        f"No dataset is called '{dataset_id}'.",
        details={"dataset_id": dataset_id[:200]},
        hint="Use a dataset_id returned by a tool, or '<run_id>/<stage_id>' for a "
        "finished pipeline stage.",
    )


class DatasetStore:
    def __init__(self, workspace: Workspace, runner: Runner) -> None:
        self.workspace = workspace
        self.runner = runner

    def reserve(self) -> tuple[str, Path]:
        """A new dataset id and its empty folder, for a stage to write into."""
        dataset_id = new_id("ds")
        folder = self.workspace.path("datasets", dataset_id)
        folder.mkdir(parents=True)
        return dataset_id, folder

    def discard(self, dataset_id: str) -> None:
        if DATASET_ID.match(dataset_id):
            shutil.rmtree(self.workspace.path("datasets", dataset_id), ignore_errors=True)

    def commit(
        self,
        dataset_id: str,
        data: StageData,
        *,
        name: str | None,
        created_by: str,
        inputs: Sequence[str] = (),
        notes: Sequence[str] = (),
        rejects: Sequence[Reject] = (),
    ) -> DatasetInfo:
        folder = self.workspace.path("datasets", dataset_id)
        files = stage_data.save(data, folder)
        if rejects:
            lines = "".join(r.model_dump_json(exclude_none=True) + "\n" for r in rejects)
            Workspace.write_bytes_atomic(folder / "rejects.jsonl", lines.encode("utf-8"))
        kind = kind_of(data)
        assert kind is not None
        info = DatasetInfo(
            dataset_id=dataset_id,
            kind=kind,
            name=name,
            item_count=item_count(data),
            rejected=len(rejects),
            created_at=now(),
            created_by=created_by,
            inputs=list(inputs),
            notes=[n[:2000] for n in notes],
            folder=folder.relative_to(self.workspace.root).as_posix(),
            files=files,
        )
        self.workspace.write_json(folder / "dataset.json", info)
        return info

    def info(self, dataset_id: str) -> DatasetInfo:
        if DATASET_ID.match(dataset_id):
            path = self.workspace.path("datasets", dataset_id, "dataset.json")
            if not path.is_file():
                raise _unknown_dataset(dataset_id)
            return DatasetInfo.model_validate(Workspace.read_json(path))
        if match := RUN_OUTPUT_ID.match(dataset_id):
            return self._run_output_info(*match.groups())
        raise _unknown_dataset(dataset_id)

    def load(self, dataset_id: str, *, limit: int | None = None) -> StageData:
        """A stored dataset; with ``limit``, only its first items, for a preview (audit
        GP-09)."""
        if DATASET_ID.match(dataset_id):
            info = self.info(dataset_id)
            folder = self.workspace.path("datasets", dataset_id)
            if limit is not None:
                return stage_data.head(info.kind, folder, limit)
            return stage_data.load(info.kind, folder)
        if match := RUN_OUTPUT_ID.match(dataset_id):
            run_id, stage_id = match.groups()
            self._run_output_info(run_id, stage_id)
            return self.runner.output(run_id, stage_id, limit=limit)
        raise _unknown_dataset(dataset_id)

    def _run_output_info(self, run_id: str, stage_id: str) -> DatasetInfo:
        store = self.runner.store
        try:
            record = store.load(run_id)
        except GeoProductError:
            raise _unknown_dataset(f"{run_id}/{stage_id}") from None
        manifest = store.read_manifest(run_id, stage_id)
        if manifest is None:
            raise GeoProductError(
                ErrorCode.not_found,
                f"Stage '{stage_id}' of run {run_id} has no finished output.",
                details={"run_id": run_id, "stage_id": stage_id},
                hint="Check get_pipeline_status; only succeeded or reused stages have output.",
            )
        plan = self.runner.plan(store.load_spec(run_id))
        folder = store.stage_dir(run_id, stage_id)
        state = next((s for s in record.stages if s.stage_id == stage_id), None)
        finished: datetime = (state.finished_at if state else None) or record.created_at
        return DatasetInfo(
            dataset_id=f"{run_id}/{stage_id}",
            kind=plan.stage(stage_id).definition.output,
            item_count=manifest.counts.output,
            rejected=manifest.counts.rejected,
            created_at=finished,
            created_by=f"pipeline {record.pipeline_name}, stage {stage_id}",
            folder=folder.relative_to(self.workspace.root).as_posix(),
            files=manifest.outputs,
        )


class SourceStore:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def folder(self, source_id: str) -> Path:
        if not SOURCE_ID.match(source_id):
            raise self._unknown(source_id)
        return self.workspace.path("sources", source_id)

    def save(self, source: Source) -> None:
        folder = self.folder(source.id)
        self.workspace.write_json(folder / "source.json", source)

    def get(self, source_id: str) -> Source:
        path = self.folder(source_id) / "source.json"
        if not path.is_file():
            raise self._unknown(source_id)
        return Source.model_validate(Workspace.read_json(path))

    def text(self, source_id: str) -> str | None:
        """The fetched text of a source, or ``None`` if it was not fetched."""
        path = self.folder(source_id) / "text.txt"
        return Workspace.read_text(path) if path.is_file() else None

    def all(self) -> list[Source]:
        root = self.workspace.path("sources")
        sources = []
        for path in sorted(root.glob("src_*/source.json")):
            try:
                sources.append(Source.model_validate(Workspace.read_json(path)))
            except (OSError, ValueError):
                continue
        return sources

    @staticmethod
    def _unknown(source_id: str) -> GeoProductError:
        return GeoProductError(
            ErrorCode.not_found,
            f"No source is called '{source_id[:200]}'.",
            details={"source_id": source_id[:200]},
            hint="Register it with register_source first.",
        )
