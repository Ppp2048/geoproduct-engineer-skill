"""Pipeline specifications: declarative, validated, and free of executable code.

A spec names registered stages only (``uses``). Values in ``with`` may reference a pipeline
parameter as ``{"param": "<name>"}`` or another stage's output as ``{"stage": "<id>"}``;
single-key objects with those keys are reserved for references.

References must point to stages defined *earlier* in the list, so a valid spec can never
contain a cycle. Whether ``uses`` names a registered stage is checked by the engine.
"""

from collections.abc import Iterator
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, model_validator

from geoproduct.schemas._base import Contract, Fraction, Slug, Text

StageName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")]
"""A registered stage, e.g. ``collect.osm_features``."""

REFERENCE_KEYS = ("param", "stage")


def iter_references(value: JsonValue) -> Iterator[tuple[str, str]]:
    """Yield ``("param", name)`` and ``("stage", id)`` references anywhere inside ``value``."""
    if isinstance(value, dict):
        if len(value) == 1:
            ((key, target),) = value.items()
            if key in REFERENCE_KEYS and isinstance(target, str):
                yield key, target
                return
        for item in value.values():
            yield from iter_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_references(item)


class RetryPolicy(Contract):
    max_attempts: Annotated[int, Field(ge=1, le=10)] = 3
    backoff_s: Annotated[float, Field(ge=0, le=60)] = 1.0
    max_backoff_s: Annotated[float, Field(ge=0, le=600)] = 30.0


class StageSpec(Contract):
    id: Slug
    uses: StageName
    input: Slug | list[Slug] | None = None
    with_: dict[str, JsonValue] = Field(default_factory=dict, alias="with")
    timeout_s: Annotated[float, Field(gt=0, le=86_400)] | None = None
    retry: RetryPolicy | None = None
    max_reject_share: Fraction | None = None
    """Share of input records a stage may reject before it fails; ``None`` = engine default."""
    description: Text | None = None

    @property
    def inputs(self) -> list[str]:
        if self.input is None:
            return []
        return [self.input] if isinstance(self.input, str) else list(self.input)

    def references(self) -> list[tuple[str, str]]:
        # Scan option values, not the ``with`` block itself: an option that happens to be
        # named ``param`` or ``stage`` is an ordinary option, not a reference.
        return [ref for value in self.with_.values() for ref in iter_references(value)]


class PipelineSpec(Contract):
    api_version: Literal["geoproduct/v1"] = Field(default="geoproduct/v1", alias="apiVersion")
    kind: Literal["Pipeline"] = "Pipeline"
    name: Slug
    description: Text | None = None
    params: dict[str, JsonValue] = Field(default_factory=dict)
    stages: Annotated[list[StageSpec], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def _references_resolve(self) -> "PipelineSpec":
        problems: list[str] = []
        defined: set[str] = set()
        for stage in self.stages:
            if stage.id in defined:
                problems.append(f"stage id '{stage.id}' is used more than once")
            refs = stage.references()
            targets = stage.inputs + [t for kind, t in refs if kind == "stage"]
            for target in targets:
                if target == stage.id:
                    problems.append(f"stage '{stage.id}' refers to itself")
                elif target not in defined:
                    problems.append(
                        f"stage '{stage.id}' refers to '{target}', which is not defined before it"
                    )
            for kind, target in refs:
                if kind == "param" and target not in self.params:
                    problems.append(f"stage '{stage.id}' uses undefined parameter '{target}'")
            defined.add(stage.id)
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def stage(self, stage_id: str) -> StageSpec:
        for stage in self.stages:
            if stage.id == stage_id:
                return stage
        raise KeyError(stage_id)
