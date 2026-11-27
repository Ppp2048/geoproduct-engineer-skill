"""The workspace: the only folder the engine writes to, plus folders a user allows for reading
and exporting.

Every path from outside (a tool argument, a pipeline parameter) goes through
:meth:`Workspace.path` or :meth:`Workspace.user_path`, which refuse anything that resolves
outside the permitted folders, including through ``..`` or links.
"""

import json
import os
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePath
from typing import Any, BinaryIO, Final

from pydantic import BaseModel

from geoproduct.errors import GeoProductError
from geoproduct.schemas.errors import ErrorCode

ENV_WORKSPACE: Final = "GEOPRODUCT_WORKSPACE"
ENV_ALLOWED_DIRS: Final = "GEOPRODUCT_ALLOWED_DIRS"
DEFAULT_WORKSPACE: Final = ".geoproduct"
SUBDIRS: Final = ("sources", "datasets", "runs", "cache")


SHARING_WAIT_S: Final = 2.0
"""How long to wait out another thread holding a workspace file (Windows only)."""


def _shared[T](action: Callable[[], T]) -> T:
    """Run a file action, waiting out a brief sharing conflict on Windows.

    Windows refuses to replace a file that another thread has open, and to open one while it
    is being replaced. Both last only as long as the other read or write, so retry for up to
    :data:`SHARING_WAIT_S`. Elsewhere, and after that, the error is raised.
    """
    delay, waited = 0.001, 0.0
    while True:
        try:
            return action()
        except PermissionError:
            if os.name != "nt" or waited >= SHARING_WAIT_S:
                raise
        time.sleep(delay)
        waited += delay
        delay = min(delay * 2, 0.05)


def _not_allowed(path: str | os.PathLike[str], why: str) -> GeoProductError:
    return GeoProductError(
        ErrorCode.path_not_allowed,
        f"Path not allowed: {why}.",
        details={"path": str(path)},
        hint=f"Use a path inside the workspace, or add its folder to {ENV_ALLOWED_DIRS}.",
    )


class Workspace:
    def __init__(
        self, root: str | os.PathLike[str], allowed_dirs: Sequence[str | os.PathLike[str]] = ()
    ):
        self.root = Path(root).resolve()
        self.allowed_dirs = tuple(Path(d).resolve() for d in allowed_dirs)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Workspace":
        env = os.environ if env is None else env
        allowed = [d for d in env.get(ENV_ALLOWED_DIRS, "").split(os.pathsep) if d.strip()]
        return cls(env.get(ENV_WORKSPACE) or DEFAULT_WORKSPACE, allowed)

    def ensure(self) -> "Workspace":
        for name in SUBDIRS:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        return self

    def path(self, *parts: str) -> Path:
        """A path inside the workspace built from relative parts.

        Parts with a drive, a root, ``..``, ``:`` or ``\\`` are refused on every platform, so
        a name that is safe on one operating system is safe on all of them.
        """
        for part in parts:
            pure = PurePath(part)
            if not part or pure.anchor or "\\" in part or ":" in part or ".." in pure.parts:
                raise _not_allowed(part, "workspace paths must be relative, without '..'")
        candidate = self.root.joinpath(*parts).resolve()
        if not candidate.is_relative_to(self.root):
            raise _not_allowed(candidate, "it resolves outside the workspace")
        return candidate

    def user_path(self, path: str | os.PathLike[str], *, must_exist: bool = True) -> Path:
        """A user-supplied file path, accepted only inside the workspace or an allowed folder."""
        resolved = Path(path).expanduser().resolve()
        if not any(resolved.is_relative_to(d) for d in (self.root, *self.allowed_dirs)):
            raise _not_allowed(path, "it is outside the workspace and the allowed folders")
        if must_exist and not resolved.is_file():
            raise GeoProductError(
                ErrorCode.not_found, "File not found.", details={"path": str(path)}
            )
        return resolved

    @staticmethod
    def write_bytes_atomic(target: Path, data: bytes) -> None:
        """Write via a temporary file and a rename, so readers never see a half-written file."""
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            _shared(lambda: os.replace(tmp, target))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def write_json(self, target: Path, value: BaseModel | Any) -> None:
        if isinstance(value, BaseModel):
            text = value.model_dump_json(indent=2, by_alias=True)
        else:
            text = json.dumps(value, indent=2, ensure_ascii=False)
        self.write_bytes_atomic(target, (text + "\n").encode("utf-8"))

    @staticmethod
    def read_text(target: Path) -> str:
        """Read a file this workspace writes, which another thread may be replacing."""
        return _shared(lambda: target.read_text(encoding="utf-8"))

    @staticmethod
    def read_bytes(target: Path) -> bytes:
        """Read a file this workspace writes, which another thread may be replacing."""
        return _shared(target.read_bytes)

    @staticmethod
    def open_binary(target: Path) -> BinaryIO:
        """Open a file this workspace writes for reading in parts, as :meth:`read_bytes`."""
        return _shared(lambda: target.open("rb"))

    @staticmethod
    def read_json(target: Path) -> Any:
        return json.loads(Workspace.read_text(target))
