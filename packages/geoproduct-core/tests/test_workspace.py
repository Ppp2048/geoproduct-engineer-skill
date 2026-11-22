import os
import threading
from pathlib import Path

import pytest

from geoproduct.errors import GeoProductError
from geoproduct.schemas import ErrorCode, GeoError
from geoproduct.workspace import DEFAULT_WORKSPACE, SUBDIRS, Workspace


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path / "ws").ensure()


def assert_refused(exc: pytest.ExceptionInfo[GeoProductError], code: ErrorCode) -> None:
    assert exc.value.code == code


class TestFromEnv:
    def test_default(self) -> None:
        ws = Workspace.from_env({})
        assert ws.root == Path(DEFAULT_WORKSPACE).resolve()
        assert ws.allowed_dirs == ()

    def test_configured(self, tmp_path: Path) -> None:
        a, b = tmp_path / "a", tmp_path / "b"
        ws = Workspace.from_env(
            {
                "GEOPRODUCT_WORKSPACE": str(tmp_path / "ws"),
                "GEOPRODUCT_ALLOWED_DIRS": f"{a}{os.pathsep}{os.pathsep}{b}",
            }
        )
        assert ws.root == (tmp_path / "ws").resolve()
        assert ws.allowed_dirs == (a.resolve(), b.resolve())


def test_ensure_creates_the_layout(ws: Workspace) -> None:
    assert all((ws.root / name).is_dir() for name in SUBDIRS)


class TestPath:
    def test_relative_parts(self, ws: Workspace) -> None:
        assert ws.path("runs", "run_1", "run.json") == ws.root / "runs" / "run_1" / "run.json"

    @pytest.mark.parametrize(
        "parts",
        [("..",), ("runs", "..", ".."), ("../outside",), ("",), ("/etc/passwd",), ("C:\\x",)],
    )
    def test_escapes_are_refused(self, ws: Workspace, parts: tuple[str, ...]) -> None:
        with pytest.raises(GeoProductError) as exc:
            ws.path(*parts)
        assert_refused(exc, ErrorCode.path_not_allowed)

    def test_links_out_of_the_workspace_are_refused(self, ws: Workspace, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        try:
            (ws.root / "sources" / "link").symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("creating symbolic links is not permitted here")
        with pytest.raises(GeoProductError) as exc:
            ws.path("sources", "link", "data.json")
        assert_refused(exc, ErrorCode.path_not_allowed)


class TestUserPath:
    def test_inside_the_workspace(self, ws: Workspace) -> None:
        file = ws.root / "sources" / "a.csv"
        file.write_text("id\n1\n", encoding="utf-8")
        assert ws.user_path(file) == file.resolve()

    def test_in_an_allowed_folder(self, tmp_path: Path) -> None:
        allowed = tmp_path / "inputs"
        allowed.mkdir()
        file = allowed / "places.geojson"
        file.write_text("{}", encoding="utf-8")
        ws = Workspace(tmp_path / "ws", [allowed])
        assert ws.user_path(str(file)) == file.resolve()

    def test_outside_is_refused(self, ws: Workspace, tmp_path: Path) -> None:
        file = tmp_path / "private.txt"
        file.write_text("x", encoding="utf-8")
        with pytest.raises(GeoProductError) as exc:
            ws.user_path(file)
        assert_refused(exc, ErrorCode.path_not_allowed)

    def test_dot_dot_back_out_is_refused(self, ws: Workspace, tmp_path: Path) -> None:
        (tmp_path / "private.txt").write_text("x", encoding="utf-8")
        with pytest.raises(GeoProductError) as exc:
            ws.user_path(ws.root / ".." / "private.txt")
        assert_refused(exc, ErrorCode.path_not_allowed)

    def test_missing_file(self, ws: Workspace) -> None:
        with pytest.raises(GeoProductError) as exc:
            ws.user_path(ws.root / "nope.csv")
        assert_refused(exc, ErrorCode.not_found)

    def test_output_paths_need_not_exist(self, ws: Workspace) -> None:
        target = ws.root / "datasets" / "out.geojson"
        assert ws.user_path(target, must_exist=False) == target.resolve()

    def test_refusal_details_are_json(self, tmp_path: Path, ws: Workspace) -> None:
        with pytest.raises(GeoProductError) as exc:
            ws.user_path(tmp_path / "x", must_exist=False)
        assert GeoError.model_validate_json(exc.value.error.model_dump_json()).details


class TestAtomicWrites:
    def test_json_round_trip(self, ws: Workspace) -> None:
        target = ws.path("runs", "run_1", "state.json")
        ws.write_json(target, {"name": "Exampletown — café", "n": 1})
        assert ws.read_json(target) == {"name": "Exampletown — café", "n": 1}
        assert target.read_bytes().endswith(b"\n")

    def test_models_are_written_with_published_names(self, ws: Workspace) -> None:
        from geoproduct.schemas import PipelineSpec

        spec = PipelineSpec.model_validate(
            {"name": "p", "stages": [{"id": "a", "uses": "transform.noop", "with": {"k": 1}}]}
        )
        target = ws.path("runs", "spec.json")
        ws.write_json(target, spec)
        data = ws.read_json(target)
        assert data["apiVersion"] == "geoproduct/v1"
        assert data["stages"][0]["with"] == {"k": 1}

    def test_overwrite_leaves_no_temporary_files(self, ws: Workspace) -> None:
        target = ws.path("cache", "x.bin")
        Workspace.write_bytes_atomic(target, b"one")
        Workspace.write_bytes_atomic(target, b"two")
        assert target.read_bytes() == b"two"
        assert [p.name for p in target.parent.iterdir()] == ["x.bin"]

    def test_failed_write_keeps_the_old_file(
        self, ws: Workspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = ws.path("cache", "x.bin")
        Workspace.write_bytes_atomic(target, b"old")

        def fail(src: str, dst: str) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", fail)
        with pytest.raises(OSError, match="disk full"):
            Workspace.write_bytes_atomic(target, b"new")
        assert target.read_bytes() == b"old"
        assert [p.name for p in target.parent.iterdir()] == ["x.bin"]


def test_files_are_replaced_safely_while_other_threads_read_them(tmp_path: Path) -> None:
    """A run record is rewritten while clients poll it. On Windows a file that is open
    cannot be replaced, nor opened while being replaced; found in Stage F, where it stopped
    a background run."""
    target = tmp_path / "run.json"
    Workspace.write_bytes_atomic(target, b"0")
    errors: list[Exception] = []
    stop = threading.Event()

    def poll() -> None:  # every millisecond: far more often than any client polls
        while not stop.wait(0.001):
            try:
                Workspace.read_text(target)
            except Exception as exc:
                errors.append(exc)
                return

    readers = [threading.Thread(target=poll) for _ in range(4)]
    for reader in readers:
        reader.start()
    try:
        for i in range(1, 201):
            Workspace.write_bytes_atomic(target, str(i).encode())
    finally:
        stop.set()
        for reader in readers:
            reader.join()
    assert not errors
    assert Workspace.read_text(target) == "200"
