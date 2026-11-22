"""What flows between stages, and how it is stored in a stage folder.

Records and buildings are written as GeoJSON so any GIS tool can open an intermediate
result; their sources and evidence go next to them in ``provenance.json``.
"""

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field

from geoproduct.engine.fingerprint import sha256_file
from geoproduct.errors import GeoProductError
from geoproduct.providers.base import BuildingFootprint
from geoproduct.schemas import (
    Contract,
    ErrorCode,
    Evidence,
    FileRef,
    QualityReport,
    Record,
    Source,
)
from geoproduct.workspace import Workspace


class DataKind(StrEnum):
    records = "records"
    buildings = "buildings"
    report = "report"
    files = "files"


class RecordSet(Contract):
    """Records together with the sources and evidence they cite."""

    records: list[Record] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)

    def merged_with(self, sources: list[Source], evidence: list[Evidence]) -> "RecordSet":
        """A copy with extra sources and evidence added, without duplicates."""
        known_sources = {s.id for s in self.sources}
        known_evidence = {e.id for e in self.evidence}
        return self.model_copy(
            update={
                "sources": self.sources + [s for s in sources if s.id not in known_sources],
                "evidence": self.evidence + [e for e in evidence if e.id not in known_evidence],
            }
        )


class BuildingSet(Contract):
    buildings: list[BuildingFootprint] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)


class FileSet(Contract):
    """Files a stage wrote, such as an export, relative to the stage folder."""

    files: list[FileRef] = Field(default_factory=list)
    copies: list[str] = Field(default_factory=list)
    """Where copies were written outside the run folder, if anywhere."""


StageData = RecordSet | BuildingSet | QualityReport | FileSet

KIND_OF: dict[type[Contract], DataKind] = {
    RecordSet: DataKind.records,
    BuildingSet: DataKind.buildings,
    QualityReport: DataKind.report,
    FileSet: DataKind.files,
}


def kind_of(data: object) -> DataKind | None:
    return KIND_OF.get(type(data))  # type: ignore[arg-type]


def item_count(data: StageData | None) -> int:
    match data:
        case RecordSet():
            return len(data.records)
        case BuildingSet():
            return len(data.buildings)
        case QualityReport():
            return len(data.findings)
        case FileSet():
            return len(data.files)
        case _:
            return 0


def file_ref(folder: Path, name: str, fmt: str, record_count: int | None = None) -> FileRef:
    path = folder / name
    return FileRef(
        path=name,
        sha256=sha256_file(path),
        format=fmt,
        size_bytes=path.stat().st_size,
        record_count=record_count,
    )


def _write(path: Path, value: Any, *, compact: bool) -> None:
    if compact:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    Workspace.write_bytes_atomic(path, (text + "\n").encode("utf-8"))


def building_feature(building: BuildingFootprint) -> dict[str, Any]:
    properties = building.model_dump(mode="json", exclude_none=True)
    geometry = properties.pop("geometry")
    feature_id = f"{building.ref.provider}:{building.ref.id}"
    return {"type": "Feature", "id": feature_id, "geometry": geometry, "properties": properties}


def save(data: StageData, folder: Path) -> list[FileRef]:
    """Write a stage's data into its folder and describe the files written."""
    folder.mkdir(parents=True, exist_ok=True)
    match data:
        case RecordSet():
            features = [record.to_feature() for record in data.records]
            _write(folder / "records.geojson", _collection(features), compact=True)
            provenance = data.model_dump(mode="json", exclude={"records"}, exclude_none=True)
            _write(folder / "provenance.json", provenance, compact=True)
            return [
                file_ref(folder, "records.geojson", "geojson", len(data.records)),
                file_ref(folder, "provenance.json", "json"),
            ]
        case BuildingSet():
            features = [building_feature(b) for b in data.buildings]
            _write(folder / "buildings.geojson", _collection(features), compact=True)
            provenance = data.model_dump(mode="json", exclude={"buildings"}, exclude_none=True)
            _write(folder / "provenance.json", provenance, compact=True)
            return [
                file_ref(folder, "buildings.geojson", "geojson", len(data.buildings)),
                file_ref(folder, "provenance.json", "json"),
            ]
        case QualityReport():
            _write(folder / "report.json", data.model_dump(mode="json"), compact=False)
            return [file_ref(folder, "report.json", "json")]
        case FileSet():
            _write(folder / "files.json", data.model_dump(mode="json"), compact=False)
            return [*data.files, file_ref(folder, "files.json", "json")]
    raise TypeError(f"not stage data: {type(data).__name__}")


def load(kind: DataKind, folder: Path) -> StageData:
    try:
        match kind:
            case DataKind.records:
                features = _read(folder / "records.geojson")["features"]
                provenance = _read(folder / "provenance.json")
                records = [Record.from_feature(f) for f in features]
                return RecordSet.model_validate({**provenance, "records": records})
            case DataKind.buildings:
                features = _read(folder / "buildings.geojson")["features"]
                provenance = _read(folder / "provenance.json")
                buildings = [{**f["properties"], "geometry": f["geometry"]} for f in features]
                return BuildingSet.model_validate({**provenance, "buildings": buildings})
            case DataKind.report:
                return QualityReport.model_validate(_read(folder / "report.json"))
            case DataKind.files:
                return FileSet.model_validate(_read(folder / "files.json"))
    except (OSError, ValueError, KeyError) as exc:
        raise GeoProductError(
            ErrorCode.internal,
            f"A stored stage output could not be read ({type(exc).__name__}).",
            details={"folder": folder.name},
            hint="Resume the run; the stage will be run again.",
        ) from exc
    raise TypeError(f"unknown data kind: {kind}")


def _collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _read(path: Path) -> Any:
    return Workspace.read_json(path)
