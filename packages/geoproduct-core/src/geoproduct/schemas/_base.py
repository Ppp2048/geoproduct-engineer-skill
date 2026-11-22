"""Building blocks shared by every published contract."""

from pathlib import PurePosixPath
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

SCHEMA_VERSION: Final = "geoproduct/v1"
SchemaVersion = Literal["geoproduct/v1"]


class Contract(BaseModel):
    """Base for every published contract.

    Unknown fields are rejected, so a typo in agent-written input fails loudly instead of
    being silently dropped.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


Identifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@-]*$"),
]
"""An id issued by the server, a provider or a user, e.g. ``run_01k...`` or ``way/123``."""

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
"""A short lowercase name: pipeline names, stage ids, parameter names, provider names."""

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

Fraction = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]

ShortText = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Text = Annotated[str, StringConstraints(min_length=1, max_length=2000)]


def _relative_posix_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or ":" in value
        or any(part in ("", "..") for part in value.split("/"))
    ):
        raise ValueError("must be a relative POSIX path without '..', drive letters or backslashes")
    return value


RelativePath = Annotated[
    str, StringConstraints(max_length=500), AfterValidator(_relative_posix_path)
]
"""A path inside a dataset or run folder, always relative and always with forward slashes."""


class FileRef(Contract):
    """A file written by the engine, with a hash so later runs can verify it is unchanged."""

    path: RelativePath
    sha256: Sha256
    format: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9.+-]{0,31}$")]
    size_bytes: Annotated[int, Field(ge=0)]
    record_count: Annotated[int, Field(ge=0)] | None = None
