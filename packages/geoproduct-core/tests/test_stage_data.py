"""Stored stage data: a preview reads only the first items of a large output (audit GP-09)."""

import json
from pathlib import Path

import pytest

from geoproduct.engine import data as stage_data
from geoproduct.engine.data import BuildingSet, DataKind, RecordSet
from geoproduct.errors import GeoProductError
from geoproduct.providers.base import BuildingFootprint
from geoproduct.schemas import ErrorCode, Evidence, Record, Source, SourceType

SOURCE = Source(id="src_test", title="Test list", type=SourceType.user_file, url_or_ref="file:x")


def record_set(count: int) -> RecordSet:
    """Records with non-ASCII names, so a small read can cut a character in half."""
    records, evidence = [], []
    for i in range(count):
        ev = Evidence(id=f"ev_{i}", source_id=SOURCE.id, supports=["name"])
        records.append(
            Record.model_validate(
                {"id": f"r-{i}", "name": f"Café Ångström {i}", "evidence_ids": [ev.id]}
            )
        )
        evidence.append(ev)
    return RecordSet(records=records, sources=[SOURCE], evidence=evidence)


def building_set(count: int) -> BuildingSet:
    buildings = []
    for i in range(count):
        x = 0.001 * i
        ring = [[x, 0.0], [x + 0.0005, 0.0], [x + 0.0005, 0.0005], [x, 0.0005], [x, 0.0]]
        buildings.append(
            BuildingFootprint.model_validate(
                {
                    "ref": {"provider": "fixture", "id": f"b-{i}"},
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                    "name": f"Häuser {i}",
                    "levels": 2,
                }
            )
        )
    return BuildingSet(buildings=buildings, sources=[SOURCE])


@pytest.mark.parametrize("chunk", [1, 7, 64 * 1024])
@pytest.mark.parametrize("limit", [0, 1, 5, 40])
def test_the_head_is_the_first_items_of_the_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chunk: int, limit: int
) -> None:
    monkeypatch.setattr(stage_data, "READ_CHUNK", chunk)
    stage_data.save(record_set(12), tmp_path / "records")
    stage_data.save(building_set(12), tmp_path / "buildings")

    whole = stage_data.load(DataKind.records, tmp_path / "records")
    head = stage_data.head(DataKind.records, tmp_path / "records", limit)
    assert isinstance(whole, RecordSet)
    assert isinstance(head, RecordSet)
    assert head.records == whole.records[:limit]

    whole_b = stage_data.load(DataKind.buildings, tmp_path / "buildings")
    head_b = stage_data.head(DataKind.buildings, tmp_path / "buildings", limit)
    assert isinstance(whole_b, BuildingSet)
    assert isinstance(head_b, BuildingSet)
    assert head_b.buildings == whole_b.buildings[:limit]


def test_the_head_does_not_read_the_whole_file(tmp_path: Path) -> None:
    """Audit GP-09: a preview loaded the whole output. The file is cut off half-way, so
    loading it fails, but its first items are still read."""
    folder = tmp_path / "records"
    stage_data.save(record_set(5000), folder)
    path = folder / "records.geojson"
    path.write_bytes(path.read_bytes()[: path.stat().st_size // 2])

    head = stage_data.head(DataKind.records, folder, 5)
    assert isinstance(head, RecordSet)
    assert [r.id for r in head.records] == ["r-0", "r-1", "r-2", "r-3", "r-4"]
    with pytest.raises(GeoProductError) as caught:
        stage_data.load(DataKind.records, folder)
    assert caught.value.code == ErrorCode.internal


def test_a_head_beyond_a_cut_off_end_is_unreadable(tmp_path: Path) -> None:
    folder = tmp_path / "records"
    stage_data.save(record_set(3), folder)
    path = folder / "records.geojson"
    path.write_bytes(path.read_bytes()[:-30])
    with pytest.raises(GeoProductError) as caught:
        stage_data.head(DataKind.records, folder, 5)
    assert caught.value.code == ErrorCode.internal


def test_a_file_laid_out_otherwise_is_loaded_whole(tmp_path: Path) -> None:
    folder = tmp_path / "records"
    stage_data.save(record_set(3), folder)
    path = folder / "records.geojson"
    path.write_text(json.dumps(json.loads(path.read_text("utf-8")), indent=2), "utf-8")
    head = stage_data.head(DataKind.records, folder, 2)
    assert isinstance(head, RecordSet)
    assert [r.id for r in head.records][:2] == ["r-0", "r-1"]
