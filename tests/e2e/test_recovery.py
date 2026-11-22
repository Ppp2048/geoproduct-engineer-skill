"""Failure and recovery, end to end: a run killed mid-stage, a provider outage, a rate
limit, missing credentials and an invalid pipeline.

The CLI tests start real ``geoproduct`` processes. Their Overpass is a stub server on the
loopback interface, and every other address goes to a dead proxy, so nothing can reach the
internet even if a check fails.
"""

import json
import os
import subprocess
import sys
import threading
from collections import Counter
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import anyio
import httpx2
import pytest
from mcp import Client

from geoproduct.engine import Runner
from geoproduct.engine.data import RecordSet
from geoproduct.providers.registry import builtin_providers
from geoproduct.workspace import Workspace
from geoproduct_mcp.server import create_server
from geoproduct_mcp.tools.common import ServerState

AGENT = "geoproduct-e2e-tests/1.0 (+https://example.org/contact)"
BOX = [0.0, 0.0, 0.01, 0.01]
CLINIC = {
    "type": "node",
    "id": 11,
    "lat": 0.0031,
    "lon": 0.0042,
    "tags": {"amenity": "clinic", "name": "Example Clinic", "addr:housenumber": "7"},
}
BUILDING = {
    "type": "way",
    "id": 21,
    "tags": {"building": "yes", "name": "Example Clinic"},
    "geometry": [
        {"lon": lon, "lat": lat}
        for lon, lat in [
            (0.0040, 0.0029),
            (0.0044, 0.0029),
            (0.0044, 0.0033),
            (0.0040, 0.0033),
            (0.0040, 0.0029),
        ]
    ],
}


def answer(*elements: dict[str, Any]) -> bytes:
    body = {"osm3s": {"timestamp_osm_base": "2026-11-01T00:00:00Z"}, "elements": list(elements)}
    return json.dumps(body).encode()


def kind_of(query: str) -> str:
    return "buildings" if 'way["building"]' in query else "features"


SPEC = f"""
apiVersion: geoproduct/v1
kind: Pipeline
name: clinic-buildings
stages:
  - id: clinics
    uses: collect.features
    with: {{provider: overpass, area: {{bbox: {BOX}}}, tags: {{amenity: clinic}}}}
  - id: buildings
    uses: collect.buildings
    with: {{provider: overpass, area: {{bbox: {BOX}}}}}
    retry: {{max_attempts: 2, backoff_s: 0}}
  - id: matched
    uses: match.buildings
    input: clinics
    with: {{buildings: {{stage: buildings}}}}
  - id: export
    uses: export.geojson
    input: matched
"""


# A stub Overpass on 127.0.0.1 -----------------------------------------------------------


class Stub:
    """Answers the clinic query (after one 429) and holds the first buildings query open
    until :attr:`release` is set, so the client can be killed while it waits."""

    def __init__(self) -> None:
        self.seen: Counter[str] = Counter()
        self.release = threading.Event()
        self.holding = threading.Event()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                form = parse_qs(self.rfile.read(length).decode())
                kind = kind_of(form.get("data", [""])[0])
                stub.seen[kind] += 1
                try:
                    if kind == "features" and stub.seen[kind] == 1:
                        self.reply(429, b'{"remark": "slow down"}', {"Retry-After": "0"})
                    elif kind == "features":
                        self.reply(200, answer(CLINIC))
                    else:
                        if not stub.release.is_set():
                            stub.holding.set()
                            stub.release.wait(60)
                        self.reply(200, answer(BUILDING))
                except OSError:
                    pass  # the client that asked has been killed

            def reply(
                self, status: int, body: bytes, headers: dict[str, str] | None = None
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/interpreter"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub() -> Iterator[Stub]:
    server = Stub()
    yield server
    server.close()


def cli_env(workspace: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GEOPRODUCT_")}
    dead_proxy = "http://127.0.0.1:9"
    env.update(
        GEOPRODUCT_WORKSPACE=str(workspace),
        HTTP_PROXY=dead_proxy,
        HTTPS_PROXY=dead_proxy,
        ALL_PROXY=dead_proxy,
        NO_PROXY="127.0.0.1",
        PYTHONIOENCODING="utf-8",
        **extra,
    )
    return env


def geoproduct(*args: str, env: dict[str, str], timeout: float = 120) -> tuple[int, Any, str]:
    """Run the CLI with ``--json``; return the exit code, the parsed output and stderr."""
    result = subprocess.run(
        [sys.executable, "-m", "geoproduct.cli", "--json", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=timeout,
        check=False,
    )
    output = json.loads(result.stdout) if result.stdout.strip() else None
    return result.returncode, output, result.stderr


def stages(record: dict[str, Any]) -> dict[str, str]:
    return {s["stage_id"]: s["status"] for s in record["stages"]}


# Tests ---------------------------------------------------------------------------------------


def test_a_run_killed_mid_stage_resumes_in_a_new_process(tmp_path: Path, stub: Stub) -> None:
    workspace = tmp_path / "ws"
    spec = tmp_path / "clinic-buildings.yaml"
    spec.write_text(SPEC, encoding="utf-8")
    env = cli_env(workspace, GEOPRODUCT_OVERPASS_URL=stub.url, GEOPRODUCT_USER_AGENT=AGENT)

    process = subprocess.Popen(
        [sys.executable, "-m", "geoproduct.cli", "run", str(spec)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )
    try:
        assert stub.holding.wait(60), "the run never reached the buildings stage"
        assert process.stdout is not None
        started = process.stdout.readline()
        assert started.startswith("Run "), started
        run_id = started.split()[1]
    finally:
        process.kill()  # no clean-up handler runs: like a crash or a power cut
        process.wait(30)

    code, record, _ = geoproduct("status", run_id, env=env)
    assert code == 0
    assert record["status"] == "running", "the killed process could not record anything"
    assert stages(record)["clinics"] == "succeeded"
    assert stages(record)["buildings"] == "running"

    stub.release.set()
    code, record, stderr = geoproduct("resume", run_id, env=env)
    assert code == 0, stderr
    assert record["status"] == "succeeded"
    assert stages(record) == {
        "clinics": "skipped",
        "buildings": "succeeded",
        "matched": "succeeded",
        "export": "succeeded",
    }
    assert stub.seen == {"features": 2, "buildings": 2}, "one 429 retry; no clinic re-query"

    matched = Runner(Workspace(workspace)).output(run_id, "matched")
    assert isinstance(matched, RecordSet)
    [clinic] = matched.records
    assert clinic.building_match is not None
    assert clinic.building_match.status.value == "exact"
    exported = workspace / "runs" / run_id / "stages" / "export" / "export.geojson"
    if not exported.exists():  # the layout is the store's; find the file wherever it is
        exported = next((workspace / "runs" / run_id).rglob("export.geojson"))
    assert json.loads(exported.read_text(encoding="utf-8"))["features"][0]["id"] == clinic.id


def test_missing_credentials_stop_the_run_before_any_request(tmp_path: Path) -> None:
    spec = tmp_path / "clinic-buildings.yaml"
    spec.write_text(SPEC, encoding="utf-8")
    env = cli_env(tmp_path / "ws")  # the public Overpass, and no User-Agent
    code, record, _ = geoproduct("run", str(spec), env=env)
    assert code == 1
    assert record["status"] == "failed"
    error = record["error"]
    assert error["code"] == "auth_missing"
    assert "GEOPRODUCT_USER_AGENT" in json.dumps(error)
    assert stages(record)["clinics"] == "failed"
    assert stages(record)["buildings"] == "pending"


BROKEN_STRUCTURE = """
apiVersion: geoproduct/v1
kind: Pipeline
name: broken
stages:
  - id: places
    uses: collect.everything
  - id: located
    uses: geocode.records
    input: later
    with: {provider: {param: geocoder}}
  - id: later
    uses: export.geojson
    input: places
"""
# The same pipeline with its structure fixed; the stages themselves are still wrong.
BROKEN_STAGES = """
apiVersion: geoproduct/v1
kind: Pipeline
name: broken
params: {geocoder: fixture}
stages:
  - id: places
    uses: collect.everything
  - id: located
    uses: geocode.records
    input: places
    with: {provider: {param: geocoder}, min_score: 7}
  - id: later
    uses: export.geojson
    input: located
"""


def test_an_invalid_pipeline_lists_every_problem_and_runs_nothing(tmp_path: Path) -> None:
    """Problems are found in two passes, the structure and then the stages, and each pass
    lists all of its problems at once."""
    workspace = tmp_path / "ws"
    env = cli_env(workspace)
    spec = tmp_path / "broken.yaml"
    for text, expected in (
        (BROKEN_STRUCTURE, ["'later', which is not defined before it", "parameter 'geocoder'"]),
        (BROKEN_STAGES, ["collect.everything", "min_score"]),
    ):
        spec.write_text(text, encoding="utf-8")
        for command in ("plan", "run"):
            code, output, _ = geoproduct(command, str(spec), env=env)
            assert code == 1, command
            assert output["error"]["code"] == "invalid_spec", command
            problems = output["error"]["details"]["problems"]
            assert len(problems) == len(expected), problems
            for words in expected:
                assert any(words in p for p in problems), (words, problems)
    runs = workspace / "runs"
    assert not runs.exists() or not any(runs.iterdir()), "no run was recorded"


def test_a_provider_outage_fails_the_run_and_resume_finishes_it(tmp_path: Path) -> None:
    """Through MCP: Overpass answers 503 for buildings until it recovers."""
    seen: Counter[str] = Counter()
    outage = {"down": True}

    def overpass(request: httpx2.Request) -> httpx2.Response:
        kind = kind_of(parse_qs(request.content.decode()).get("data", [""])[0])
        seen[kind] += 1
        if kind == "buildings" and outage["down"]:
            return httpx2.Response(503, text="overloaded", headers={"Retry-After": "0"})
        body = answer(BUILDING) if kind == "buildings" else answer(CLINIC)
        return httpx2.Response(200, content=body, headers={"content-type": "application/json"})

    env = {
        "GEOPRODUCT_USER_AGENT": AGENT,
        "GEOPRODUCT_OVERPASS_URL": "https://overpass.example.org/api/interpreter",
    }
    workspace = Workspace(tmp_path / "ws").ensure()
    providers = builtin_providers(workspace, env, transport=httpx2.MockTransport(overpass))
    state = ServerState(workspace, providers=providers, env=env)
    results: dict[str, Any] = {}

    async def session() -> None:
        async with Client(create_server(state)) as client:

            async def status(run_id: str) -> dict[str, Any]:
                await anyio.to_thread.run_sync(lambda: state.jobs.wait(run_id, timeout=60))
                result = await client.call_tool("get_pipeline_status", {"run_id": run_id})
                assert result.structured_content is not None
                return dict(result.structured_content)

            started = await client.call_tool("run_geo_pipeline", {"spec": SPEC})
            assert not started.is_error, started.content
            assert started.structured_content is not None
            run_id = started.structured_content["run_id"]
            results["failed"] = await status(run_id)
            results["tries"] = seen["buildings"]
            outage["down"] = False
            resumed = await client.call_tool("run_geo_pipeline", {"run_id": run_id})
            assert not resumed.is_error, resumed.content
            results["resumed"] = await status(run_id)

    anyio.run(session)

    failed = results["failed"]["run"]
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "provider_unavailable"
    assert failed["error"]["stage"] == "buildings"
    assert failed["error"]["retryable"] is True
    assert stages(failed)["clinics"] == "succeeded"
    assert results["tries"] == 6, "3 HTTP tries in each of 2 stage attempts"

    resumed = results["resumed"]["run"]
    assert resumed["status"] == "succeeded"
    assert stages(resumed)["clinics"] == "skipped"
    assert seen["features"] == 1, "the finished stage was not run again"
    assert seen["buildings"] == 7
