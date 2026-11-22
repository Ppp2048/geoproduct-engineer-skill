"""Run records and stage manifests: what ran, with what, and what came out."""

from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, Field, JsonValue

from geoproduct.schemas._base import (
    SCHEMA_VERSION,
    Contract,
    FileRef,
    Identifier,
    SchemaVersion,
    Sha256,
    ShortText,
    Slug,
)
from geoproduct.schemas.errors import GeoError
from geoproduct.schemas.pipelines import StageName


class RunStatus(StrEnum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class StageStatus(StrEnum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    skipped = "skipped"
    """Reused from an earlier attempt because its checkpoint still matches."""
    failed = "failed"
    cancelled = "cancelled"


Count = Annotated[int, Field(ge=0)]


class Counts(Contract):
    input: Count = 0
    output: Count = 0
    rejected: Count = 0


class Progress(Contract):
    done: Count
    total: Count | None = None
    message: ShortText | None = None


class StageState(Contract):
    stage_id: Slug
    uses: StageName
    status: StageStatus = StageStatus.pending
    attempts: Count = 0
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    fingerprint: Sha256 | None = None
    counts: Counts = Field(default_factory=Counts)
    progress: Progress | None = None
    error: GeoError | None = None


class RunRecord(Contract):
    schema_version: SchemaVersion = SCHEMA_VERSION
    run_id: Identifier
    pipeline_name: Slug
    spec_sha256: Sha256
    status: RunStatus = RunStatus.pending
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    current_stage: Slug | None = None
    cancel_requested: bool = False
    stages: list[StageState]
    versions: dict[ShortText, ShortText] = Field(default_factory=dict)
    """Library and interpreter versions used, for reproducibility."""
    error: GeoError | None = None


class ProviderUse(Contract):
    provider: Slug
    endpoint: ShortText | None = None
    requests: Count = 0
    cache_hits: Count = 0
    data_timestamp: ShortText | None = None
    licence: ShortText | None = None


class StageManifest(Contract):
    """Written next to a stage's outputs; the checkpoint that makes resuming safe."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    run_id: Identifier
    stage_id: Slug
    uses: StageName
    stage_version: ShortText
    fingerprint: Sha256
    input_fingerprints: list[Sha256] = Field(default_factory=list)
    params: dict[str, JsonValue] = Field(default_factory=dict)
    outputs: list[FileRef] = Field(default_factory=list)
    counts: Counts = Field(default_factory=Counts)
    providers: list[ProviderUse] = Field(default_factory=list)
    source_ids: list[Identifier] = Field(default_factory=list)
    started_at: AwareDatetime
    finished_at: AwareDatetime
