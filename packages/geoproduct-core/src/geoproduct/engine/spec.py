"""Loading pipeline specs and compiling them into a checked plan.

Compiling checks what the schema alone cannot: that every stage is registered, that each
stage's options match its parameter model once ``{"param": ...}`` references are filled
in, and that connected stages agree on the kind of data passed between them. All problems
are reported together, so an agent can fix a spec in one pass.
"""

import copy
import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, JsonValue, ValidationError

from geoproduct.engine.data import DataKind
from geoproduct.engine.fingerprint import sha256_json
from geoproduct.engine.stage import StageDef, StageRef, StageRegistry
from geoproduct.errors import GeoProductError
from geoproduct.schemas import ErrorCode, PipelineSpec, StageSpec

MAX_SPEC_BYTES = 1_000_000


def parse_spec(text: str) -> PipelineSpec:
    """Parse a YAML or JSON spec. YAML is read with the safe loader only."""
    if len(text.encode("utf-8")) > MAX_SPEC_BYTES:
        raise GeoProductError(
            ErrorCode.too_large,
            f"The spec is larger than {MAX_SPEC_BYTES // 1000} kB.",
            hint="Put large inline data in a file and read it with collect.file.",
        )
    try:
        data = json.loads(text) if text.lstrip().startswith("{") else yaml.safe_load(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise GeoProductError(
            ErrorCode.invalid_spec, f"The spec is not valid YAML or JSON: {exc}"
        ) from None
    if not isinstance(data, dict):
        raise GeoProductError(ErrorCode.invalid_spec, "The spec must be a mapping at the top.")
    return validate_spec(data)


def validate_spec(data: Mapping[str, Any]) -> PipelineSpec:
    try:
        return PipelineSpec.model_validate(data)
    except ValidationError as exc:
        problems = [line for error in exc.errors() for line in _describe(error)]
        raise GeoProductError(
            ErrorCode.invalid_spec,
            f"The spec has {len(problems)} problem(s).",
            details={"problems": list(problems)},
            hint="Fix every listed problem; they were all found in one pass.",
        ) from None


def load_spec_file(path: str | os.PathLike[str]) -> PipelineSpec:
    path = Path(path)
    if path.stat().st_size > MAX_SPEC_BYTES:
        raise GeoProductError(ErrorCode.too_large, "The spec file is too large.")
    return parse_spec(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class PlannedStage:
    spec: StageSpec
    definition: StageDef[Any]
    params: BaseModel
    input: str | None
    refs: tuple[str, ...]
    """Ids of the stages referenced in the parameters, in order of first appearance."""

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ((self.input,) if self.input else ()) + self.refs


@dataclass(frozen=True)
class Plan:
    spec: PipelineSpec
    stages: tuple[PlannedStage, ...]
    spec_sha256: str

    def stage(self, stage_id: str) -> PlannedStage:
        for planned in self.stages:
            if planned.id == stage_id:
                return planned
        raise KeyError(stage_id)

    def describe(self) -> list[dict[str, JsonValue]]:
        steps: list[dict[str, JsonValue]] = []
        for planned in self.stages:
            provider = getattr(planned.params, "provider", None)
            steps.append(
                {
                    "id": planned.id,
                    "uses": planned.definition.name,
                    "input": planned.input,
                    "references": list(planned.refs),
                    "output": planned.definition.output.value,
                    "provider": provider if isinstance(provider, str) else None,
                    "retry_safe": planned.definition.retry_safe,
                }
            )
        return steps


def with_params(spec: PipelineSpec, params: Mapping[str, JsonValue]) -> PipelineSpec:
    """The spec with some declared parameters replaced."""
    if not params:
        return spec
    unknown = sorted(set(params) - set(spec.params))
    if unknown:
        raise GeoProductError(
            ErrorCode.invalid_input,
            f"The pipeline declares no parameter(s): {', '.join(unknown)}.",
            details={"unknown": unknown, "declared": sorted(spec.params)},
            hint="Only parameters listed under 'params' in the spec can be set.",
        )
    data = spec.model_dump(mode="json", by_alias=True)
    data["params"] = {**data["params"], **params}
    return validate_spec(data)


def substitute_params(value: JsonValue, params: Mapping[str, JsonValue]) -> JsonValue:
    """Replace every ``{"param": name}`` with a copy of that parameter's value."""
    if isinstance(value, dict):
        if len(value) == 1 and isinstance(value.get("param"), str):
            return copy.deepcopy(params[str(value["param"])])
        return {key: substitute_params(item, params) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute_params(item, params) for item in value]
    return value


def find_stage_refs(value: object) -> Iterator[StageRef]:
    if isinstance(value, StageRef):
        yield value
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from find_stage_refs(getattr(value, name))
    elif isinstance(value, dict):
        for item in value.values():
            yield from find_stage_refs(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from find_stage_refs(item)


def compile_plan(spec: PipelineSpec, registry: StageRegistry) -> Plan:
    problems: list[str] = []
    produced: dict[str, DataKind] = {}
    stages: list[PlannedStage] = []

    for stage_spec in spec.stages:
        where = f"stage '{stage_spec.id}'"
        try:
            definition = registry.get(stage_spec.uses)
        except GeoProductError as exc:
            similar = exc.error.details.get("similar")
            names = [str(s) for s in similar] if isinstance(similar, list) else []
            suggestion = f" (did you mean {', '.join(names)}?)" if names else ""
            problems.append(f"{where}: no registered stage '{stage_spec.uses}'{suggestion}")
            continue

        inputs = stage_spec.inputs
        if definition.input is None and inputs:
            problems.append(f"{where}: {definition.name} takes no input")
        elif definition.input is not None and not inputs:
            problems.append(f"{where}: {definition.name} needs an input of {definition.input}")
        elif len(inputs) > 1:
            problems.append(f"{where}: {definition.name} takes one input, not {len(inputs)}")
        elif inputs and inputs[0] in produced and produced[inputs[0]] != definition.input:
            problems.append(
                f"{where}: input '{inputs[0]}' produces {produced[inputs[0]]}, "
                f"but {definition.name} needs {definition.input}"
            )

        options = substitute_params(dict(stage_spec.with_), spec.params)
        try:
            params = definition.params.model_validate(options)
        except ValidationError as exc:
            problems.extend(
                f"{where}: with.{line}" for error in exc.errors() for line in _describe(error)
            )
            produced[stage_spec.id] = definition.output
            continue

        refs: list[str] = []
        for ref in find_stage_refs(params):
            if ref.stage not in refs:
                refs.append(ref.stage)
            kind = produced.get(ref.stage)
            if kind is not None and kind not in definition.ref_kinds:
                accepted = ", ".join(sorted(definition.ref_kinds)) or "no stage references"
                problems.append(
                    f"{where}: '{ref.stage}' produces {kind}, but {definition.name} "
                    f"accepts {accepted}"
                )

        produced[stage_spec.id] = definition.output
        stages.append(
            PlannedStage(
                spec=stage_spec,
                definition=definition,
                params=params,
                input=inputs[0] if inputs else None,
                refs=tuple(refs),
            )
        )

    if problems:
        raise GeoProductError(
            ErrorCode.invalid_spec,
            f"The pipeline has {len(problems)} problem(s).",
            details={"problems": list(problems)},
            hint="Fix every listed problem; they were all found in one pass.",
        )
    spec_json = spec.model_dump(mode="json", by_alias=True)
    return Plan(spec=spec, stages=tuple(stages), spec_sha256=sha256_json(spec_json))


def _describe(error: Any) -> list[str]:
    loc = error.get("loc", ())
    cause = (error.get("ctx") or {}).get("error")
    if not loc and error.get("type") == "value_error" and cause is not None:
        # The spec's own cross-checks (stage references, parameters): one line per problem,
        # without pydantic's "Value error," prefix.
        return str(cause).split("; ")
    location = ".".join(str(part) for part in loc) or "(top)"
    return [f"{location}: {error.get('msg', 'invalid')}"]
