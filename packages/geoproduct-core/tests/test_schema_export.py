"""The published JSON Schemas must match the models and be valid draft 2020-12 schemas."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from geoproduct.schemas import PipelineSpec, Record, Source
from geoproduct.schemas.export import BASE_ID, DIALECT, PUBLISHED, json_schema, render

REGENERATE = "regenerate with: uv run python -m geoproduct.schemas.export schemas/v1"


def schema_dir(repo_root: Path) -> Path:
    return repo_root / "schemas" / "v1"


@pytest.mark.parametrize("name", sorted(PUBLISHED))
def test_published_file_is_current(repo_root: Path, name: str) -> None:
    path = schema_dir(repo_root) / f"{name}.schema.json"
    assert path.is_file(), f"{path.name} is missing; {REGENERATE}"
    assert path.read_text(encoding="utf-8") == render(name), f"{path.name} is stale; {REGENERATE}"


def test_no_unpublished_files(repo_root: Path) -> None:
    on_disk = {p.name for p in schema_dir(repo_root).glob("*.schema.json")}
    assert on_disk == {f"{name}.schema.json" for name in PUBLISHED}


@pytest.mark.parametrize("name", sorted(PUBLISHED))
def test_valid_draft_2020_12(name: str) -> None:
    schema = json_schema(name)
    assert schema["$schema"] == DIALECT
    assert schema["$id"] == f"{BASE_ID}{name}.schema.json"
    Draft202012Validator.check_schema(schema)


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(json_schema(name))


def test_models_and_schemas_agree_on_a_record(record_data: dict[str, Any]) -> None:
    dumped = Record.model_validate(record_data).model_dump(mode="json", by_alias=True)
    validator("record").validate(dumped)


def test_models_and_schemas_agree_on_a_source(evaluation: Any) -> None:
    source = Source(
        id="src_1",
        title="Example",
        type="osm",
        url_or_ref="https://www.openstreetmap.org/",
        licence="ODbL-1.0",
        evaluation=evaluation(2),
    )
    validator("source").validate(source.model_dump(mode="json", by_alias=True))


def test_pipeline_spec_schema_uses_published_names() -> None:
    raw = {
        "apiVersion": "geoproduct/v1",
        "kind": "Pipeline",
        "name": "clinics",
        "params": {"q": "clinic"},
        "stages": [{"id": "a", "uses": "collect.features", "with": {"q": {"param": "q"}}}],
    }
    validator("pipeline-spec").validate(raw)
    PipelineSpec.model_validate(raw)
    assert not validator("pipeline-spec").is_valid({**raw, "kind": "Job"})


def test_schemas_reject_what_the_models_reject() -> None:
    assert not validator("record").is_valid({"id": "rec_1", "nmae": "typo"})
    assert not validator("area").is_valid({"bbox": [0, 0, 181, 1]})


def test_files_are_plain_utf8_json_with_lf(repo_root: Path) -> None:
    for path in schema_dir(repo_root).glob("*.schema.json"):
        raw = path.read_bytes()
        assert b"\r\n" not in raw
        json.loads(raw.decode("utf-8"))
