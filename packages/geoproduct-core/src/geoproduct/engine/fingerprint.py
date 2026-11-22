"""Content hashes. Checkpoints are reused only when these still match."""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from geoproduct.schemas import FileRef


def canonical_json(value: Any) -> bytes:
    """One byte representation per value: sorted keys, no spaces, no NaN."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def digest_files(files: Sequence[FileRef]) -> str:
    """One hash for a stage's outputs, independent of the order they were written in."""
    return sha256_json(sorted([f.path, f.sha256] for f in files))


def short_id(prefix: str, *parts: Any) -> str:
    """A deterministic id such as ``src_3f9a…`` (24 hex characters) for derived objects.

    Sources and evidence created by stages get the same id for the same input, so re-running
    a stage on unchanged input produces byte-identical output.
    """
    return f"{prefix}_{sha256_json(list(parts))[:24]}"
