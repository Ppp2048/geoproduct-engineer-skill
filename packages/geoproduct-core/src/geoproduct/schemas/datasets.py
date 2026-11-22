"""Dataset manifests: what a stored or exported dataset contains and where it came from."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from geoproduct.schemas._base import (
    SCHEMA_VERSION,
    Contract,
    FileRef,
    Identifier,
    SchemaVersion,
    ShortText,
    Slug,
    Text,
)
from geoproduct.schemas.geometry import BBox


class LicenceNotice(Contract):
    """A licence that applies to part of a dataset, and the sources it comes from."""

    licence: ShortText
    source_ids: Annotated[list[Identifier], Field(min_length=1)]
    attribution: Text | None = None
    share_alike: bool = False
    """True when derived data must be published under the same licence (e.g. ODbL)."""


class DatasetManifest(Contract):
    schema_version: SchemaVersion = SCHEMA_VERSION
    id: Identifier
    name: ShortText
    description: Text | None = None
    created_at: AwareDatetime
    crs: Literal["EPSG:4326"] = "EPSG:4326"
    record_count: Annotated[int, Field(ge=0)]
    evidence_count: Annotated[int, Field(ge=0)] = 0
    bbox: BBox | None = None
    source_ids: list[Identifier] = Field(default_factory=list)
    run_id: Identifier | None = None
    stage_id: Slug | None = None
    licences: list[LicenceNotice] = Field(default_factory=list)
    files: list[FileRef] = Field(default_factory=list)
