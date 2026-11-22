from typing import Any

import pytest
from pydantic import ValidationError

from geoproduct.schemas import PipelineSpec
from geoproduct.schemas.pipelines import iter_references


def spec(*stages: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "apiVersion": "geoproduct/v1",
        "kind": "Pipeline",
        "name": "clinics",
        "params": {"area": {"bbox": [0, 0, 0.01, 0.01]}} if params is None else params,
        "stages": list(stages),
    }


COLLECT = {"id": "collect", "uses": "collect.features", "with": {"area": {"param": "area"}}}
MATCH = {"id": "match", "uses": "resolve.buildings", "input": "collect"}


def test_valid_spec() -> None:
    pipeline = PipelineSpec.model_validate(spec(COLLECT, MATCH))
    assert pipeline.stage("match").inputs == ["collect"]
    assert pipeline.stage("collect").references() == [("param", "area")]
    assert pipeline.stage("collect").with_ == {"area": {"param": "area"}}


def test_dump_uses_the_published_field_names() -> None:
    dumped = PipelineSpec.model_validate(spec(COLLECT)).model_dump(by_alias=True)
    assert dumped["apiVersion"] == "geoproduct/v1"
    assert "with" in dumped["stages"][0]


def test_unknown_stage_lookup() -> None:
    with pytest.raises(KeyError):
        PipelineSpec.model_validate(spec(COLLECT)).stage("missing")


def test_references_may_only_point_backwards() -> None:
    with pytest.raises(ValidationError, match="'match' refers to 'collect', which is not defined"):
        PipelineSpec.model_validate(spec(MATCH, COLLECT))


def test_stage_references_inside_options_are_checked() -> None:
    later = {"id": "join", "uses": "combine.join", "with": {"right": {"stage": "later"}}}
    with pytest.raises(ValidationError, match="'later'"):
        PipelineSpec.model_validate(spec(COLLECT, later))


def test_self_reference() -> None:
    loop = {"id": "loop", "uses": "transform.noop", "input": "loop"}
    with pytest.raises(ValidationError, match="refers to itself"):
        PipelineSpec.model_validate(spec(loop))


def test_duplicate_ids() -> None:
    with pytest.raises(ValidationError, match="used more than once"):
        PipelineSpec.model_validate(spec(COLLECT, COLLECT))


def test_undefined_parameter() -> None:
    with pytest.raises(ValidationError, match="undefined parameter 'area'"):
        PipelineSpec.model_validate(spec(COLLECT, params={}))


def test_all_problems_are_reported_at_once() -> None:
    with pytest.raises(ValidationError) as info:
        PipelineSpec.model_validate(spec(MATCH, COLLECT, params={}))
    message = str(info.value)
    assert "not defined before it" in message
    assert "undefined parameter" in message


def test_an_option_named_like_a_reference_is_an_ordinary_option() -> None:
    stage = {"id": "pick", "uses": "transform.pick", "with": {"stage": "anything"}}
    assert PipelineSpec.model_validate(spec(stage)).stage("pick").references() == []


@pytest.mark.parametrize("uses", ["collect", "Collect.features", "collect.", "collect..x", "1.x"])
def test_uses_must_name_a_dotted_stage(uses: str) -> None:
    with pytest.raises(ValidationError):
        PipelineSpec.model_validate(spec({"id": "s", "uses": uses}))


@pytest.mark.parametrize("bad", [{"stages": []}, {"name": "Not A Slug"}, {"kind": "Job"}])
def test_spec_level_rules(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PipelineSpec.model_validate({**spec(COLLECT), **bad})


def test_at_most_one_hundred_stages() -> None:
    stages = [{"id": f"s{i}", "uses": "transform.noop"} for i in range(101)]
    with pytest.raises(ValidationError):
        PipelineSpec.model_validate(spec(*stages))


def test_executable_looking_fields_are_rejected() -> None:
    """A spec is data: there is no field that could carry code to run."""
    with pytest.raises(ValidationError, match="Extra inputs"):
        PipelineSpec.model_validate(spec({**COLLECT, "run": "import os"}))


def test_iter_references_walks_nested_values() -> None:
    value = {
        "a": [{"param": "x"}, {"nested": {"stage": "s1"}}],
        "b": {"param": "y", "other": 1},
        "c": {"param": 5},
    }
    assert sorted(iter_references(value)) == [("param", "x"), ("stage", "s1")]
