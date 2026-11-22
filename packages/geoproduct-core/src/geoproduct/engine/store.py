"""The run store: one folder per run inside the workspace.

    runs/<run_id>/spec.json        the spec as run (parameters included)
    runs/<run_id>/run.json         status, timings, counts and errors (RunRecord)
    runs/<run_id>/events.jsonl     append-only execution log
    runs/<run_id>/cancel           present while a cancellation is requested
    runs/<run_id>/stages/<id>/     outputs, rejects.jsonl and manifest.json

A stage's ``manifest.json`` is written last. It is the checkpoint: without it, or when its
fingerprint or output hashes no longer match, the stage runs again.
"""

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from geoproduct.engine.fingerprint import sha256_file
from geoproduct.engine.stage import Reject
from geoproduct.errors import GeoProductError
from geoproduct.schemas import (
    ErrorCode,
    PipelineSpec,
    RunRecord,
    StageManifest,
)
from geoproduct.workspace import Workspace

RUN_ID = re.compile(r"^run_[0-9a-z]{26}$")


def now() -> datetime:
    return datetime.now(UTC)


class RunStore:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def run_dir(self, run_id: str) -> Path:
        if not RUN_ID.match(run_id):
            raise GeoProductError(
                ErrorCode.invalid_input,
                "That is not a run id.",
                details={"run_id": run_id[:100]},
                hint="Run ids look like run_ followed by 26 letters and digits.",
            )
        return self.workspace.path("runs", run_id)

    def exists(self, run_id: str) -> bool:
        return (self.run_dir(run_id) / "run.json").is_file()

    def create(self, record: RunRecord, spec: PipelineSpec) -> None:
        folder = self.run_dir(record.run_id)
        folder.mkdir(parents=True, exist_ok=False)
        self.save_spec(record.run_id, spec)
        self.save(record)
        self.event(record.run_id, "run_created", pipeline=record.pipeline_name)

    def load(self, run_id: str) -> RunRecord:
        path = self.run_dir(run_id) / "run.json"
        if not path.is_file():
            raise GeoProductError(ErrorCode.not_found, "No such run.", details={"run_id": run_id})
        return RunRecord.model_validate_json(Workspace.read_text(path))

    def save(self, record: RunRecord) -> None:
        self.workspace.write_json(self.run_dir(record.run_id) / "run.json", record)

    def load_spec(self, run_id: str) -> PipelineSpec:
        path = self.run_dir(run_id) / "spec.json"
        return PipelineSpec.model_validate_json(Workspace.read_text(path))

    def save_spec(self, run_id: str, spec: PipelineSpec) -> None:
        self.workspace.write_json(self.run_dir(run_id) / "spec.json", spec)

    def event(self, run_id: str, event: str, **fields: object) -> None:
        entry = {"ts": now().isoformat(timespec="milliseconds"), "event": event, **fields}
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with (self.run_dir(run_id) / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def events(self, run_id: str) -> list[dict[str, Any]]:
        path = self.run_dir(run_id) / "events.jsonl"
        if not path.is_file():
            return []
        lines = Workspace.read_text(path).splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def stage_dir(self, run_id: str, stage_id: str) -> Path:
        return self.run_dir(run_id) / "stages" / stage_id

    def reset_stage(self, run_id: str, stage_id: str) -> None:
        """Remove a stage's previous outputs before it runs again."""
        folder = self.stage_dir(run_id, stage_id)
        if folder.exists():
            shutil.rmtree(folder)

    def write_manifest(self, manifest: StageManifest) -> None:
        folder = self.stage_dir(manifest.run_id, manifest.stage_id)
        self.workspace.write_json(folder / "manifest.json", manifest)

    def read_manifest(self, run_id: str, stage_id: str) -> StageManifest | None:
        path = self.stage_dir(run_id, stage_id) / "manifest.json"
        try:
            return StageManifest.model_validate_json(Workspace.read_text(path))
        except (OSError, ValidationError):
            return None

    def outputs_intact(self, manifest: StageManifest) -> bool:
        folder = self.stage_dir(manifest.run_id, manifest.stage_id)
        for ref in manifest.outputs:
            path = folder / ref.path
            if not path.is_file() or sha256_file(path) != ref.sha256:
                return False
        return True

    def write_rejects(self, run_id: str, stage_id: str, rejects: list[Reject]) -> None:
        lines = "".join(reject.model_dump_json(exclude_none=True) + "\n" for reject in rejects)
        path = self.stage_dir(run_id, stage_id) / "rejects.jsonl"
        Workspace.write_bytes_atomic(path, lines.encode("utf-8"))

    def request_cancel(self, run_id: str) -> None:
        Workspace.write_bytes_atomic(self.run_dir(run_id) / "cancel", b"cancel requested\n")

    def cancel_requested(self, run_id: str) -> bool:
        return (self.run_dir(run_id) / "cancel").exists()

    def clear_cancel(self, run_id: str) -> None:
        (self.run_dir(run_id) / "cancel").unlink(missing_ok=True)

    def run_ids(self) -> list[str]:
        root = self.workspace.path("runs")
        if not root.is_dir():
            return []
        return sorted(p.name for p in root.iterdir() if RUN_ID.match(p.name))
