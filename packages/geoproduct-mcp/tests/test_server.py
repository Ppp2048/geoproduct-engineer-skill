"""The server as a client sees it, through the SDK's in-process client."""

import json
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecurityMiddleware
from mcp_types import TextResourceContents

from geoproduct.providers.fixture import AREA_BBOX
from geoproduct.schemas import Contract
from geoproduct.stages.quality import CHECKS
from geoproduct_mcp.calls import call
from geoproduct_mcp.server import create_server, main, transport_security
from geoproduct_mcp.tools.common import Done, Job, ServerState

TOOLS = {
    "register_source",
    "fetch_source_data",
    "geocode",
    "reverse_geocode",
    "geocode_records",
    "search_osm_features",
    "get_building_footprints",
    "create_dataset",
    "normalize_records",
    "deduplicate_entities",
    "match_building_footprints",
    "enrich_records",
    "score_confidence",
    "validate_dataset",
    "generate_quality_report",
    "export_geo_dataset",
    "create_geo_pipeline",
    "run_geo_pipeline",
    "get_pipeline_status",
    "get_pipeline_results",
    "cancel_geo_pipeline",
}


def connected(state: ServerState, test: Callable[[Client], Awaitable[None]]) -> None:
    async def run() -> None:
        async with Client(create_server(state)) as client:
            await test(client)

    anyio.run(run)


def text_of(contents: Any) -> str:
    first = contents.contents[0]
    assert isinstance(first, TextResourceContents)
    return first.text


def test_every_tool_is_listed_with_an_output_schema(state: ServerState) -> None:
    async def check(client: Client) -> None:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == TOOLS
        for tool in tools.values():
            assert tool.output_schema, tool.name
            assert tool.description, tool.name
            assert tool.annotations is not None, tool.name
        assert tools["get_pipeline_status"].annotations.read_only_hint is True  # type: ignore[union-attr]
        assert tools["fetch_source_data"].annotations.open_world_hint is True  # type: ignore[union-attr]
        checks = tools["validate_dataset"].input_schema["properties"]["checks"]["description"]
        assert all(name in checks for name in CHECKS), "the description names every check"

    connected(state, check)


def test_results_are_structured_and_summarised(state: ServerState) -> None:
    async def check(client: Client) -> None:
        result = await client.call_tool(
            "search_osm_features",
            {
                "area": {"bbox": list(AREA_BBOX)},
                "tags": {"amenity": "hospital"},
                "provider": "fixture",
            },
        )
        assert not result.is_error
        assert result.structured_content is not None
        dataset = result.structured_content["dataset"]
        assert dataset["dataset_id"].startswith("ds_")
        assert "Dataset ds_" in result.content[0].text  # type: ignore[union-attr]

    connected(state, check)


def test_failures_carry_a_structured_error(state: ServerState) -> None:
    async def check(client: Client) -> None:
        result = await client.call_tool("normalize_records", {"dataset_id": "ds_missing"})
        assert result.is_error
        assert result.structured_content is not None
        error = result.structured_content["error"]
        assert error["code"] == "not_found"
        assert error["hint"]
        text = result.content[0].text  # type: ignore[union-attr]
        assert text.startswith("not_found:")

    connected(state, check)


def test_arguments_are_validated_against_the_schema(state: ServerState) -> None:
    async def check(client: Client) -> None:
        result = await client.call_tool("get_pipeline_status", {"run_id": "../../etc"})
        assert result.is_error

    connected(state, check)


def test_resources_describe_the_server(state: ServerState) -> None:
    async def check(client: Client) -> None:
        guide = text_of(await client.read_resource("geoproduct://guide"))
        assert "Never invent coordinates" in guide
        stages = json.loads(text_of(await client.read_resource("geoproduct://stages")))
        assert "collect.features" in {s["name"] for s in stages}
        providers = json.loads(text_of(await client.read_resource("geoproduct://providers")))
        by_name = {p["name"]: p for p in providers}
        assert by_name["nominatim"]["policy"]["max_batch"] == 100
        assert "GEOPRODUCT_USER_AGENT" not in json.dumps(by_name["nominatim"]["credentials"])
        schema = json.loads(text_of(await client.read_resource("geoproduct://schemas/v1/record")))
        assert schema["title"] == "Record"
        plan = await client.call_tool(
            "create_geo_pipeline",
            {
                "spec": {
                    "apiVersion": "geoproduct/v1",
                    "kind": "Pipeline",
                    "name": "one",
                    "stages": [{"id": "a", "uses": "collect.records", "with": {"records": []}}],
                }
            },
        )
        assert plan.structured_content is not None
        run_id = plan.structured_content["run_id"]
        run = json.loads(text_of(await client.read_resource(f"geoproduct://runs/{run_id}")))
        assert run["run"]["status"] == "pending"
        assert run["events"][0]["event"] == "run_created"
        # Audit GP-04: a torn last line made this resource fail.
        log = state.runner.store.run_dir(run_id) / "events.jsonl"
        with log.open("ab") as handle:
            handle.write(b'{"ts":"2026-01-01","event":"run_sta')
        run = json.loads(text_of(await client.read_resource(f"geoproduct://runs/{run_id}")))
        assert [e["event"] for e in run["events"]] == ["run_created"]

    connected(state, check)


def test_prompts_render(state: ServerState) -> None:
    async def check(client: Client) -> None:
        names = {p.name for p in (await client.list_prompts()).prompts}
        assert names == {"plan_geographic_dataset", "audit_dataset_quality"}
        prompt = await client.get_prompt(
            "plan_geographic_dataset", {"goal": "every public library in a sample county"}
        )
        message = prompt.messages[0].content
        assert "every public library" in message.text  # type: ignore[union-attr]

    connected(state, check)


class Empty(Contract):
    pass


def test_a_cancelled_call_sets_the_work_cancel_flag() -> None:
    started = threading.Event()
    seen: dict[str, bool] = {}

    def work(job: Job) -> Done:
        started.set()
        seen["cancelled"] = job.cancel.wait(5)
        return Done(Empty(), "done")

    async def run() -> None:
        async with anyio.create_task_group() as group:
            group.start_soon(call, work)
            await anyio.to_thread.run_sync(started.wait)
            group.cancel_scope.cancel()

    anyio.run(run)
    deadline = time.monotonic() + 5
    while "cancelled" not in seen and time.monotonic() < deadline:
        time.sleep(0.01)
    assert seen == {"cancelled": True}


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.org"])
def test_http_is_only_served_on_loopback(host: str) -> None:
    assert main(["--http", "--host", host]) == 2


def http_statuses(app: Any, requests: list[tuple[str, str | None]]) -> list[int]:
    """POST each (Host, Origin) request to the Streamable HTTP app in-process, and return
    the response statuses. The body is not JSON: a request that passes the Host and Origin
    checks is answered 400."""
    statuses: list[int] = []

    async def post(host: str, origin: str | None) -> None:
        headers = [
            (b"host", host.encode()),
            (b"content-type", b"application/json"),
            (b"accept", b"application/json, text/event-stream"),
        ]
        if origin is not None:
            headers.append((b"origin", origin.encode()))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/mcp",
            "raw_path": b"/mcp",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.2", 50000),
            "server": ("127.0.0.2", 8123),
        }
        messages = [{"type": "http.request", "body": b"not json", "more_body": False}]

        async def receive() -> dict[str, Any]:
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                statuses.append(message["status"])

        await app(scope, receive, send)

    async def run() -> None:
        with anyio.fail_after(10):
            async with app.router.lifespan_context(app):
                for host, origin in requests:
                    await post(host, origin)

    anyio.run(run)
    return statuses


def test_another_loopback_address_is_protected_from_dns_rebinding(
    state: ServerState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit GP-05: the SDK protects only 127.0.0.1, localhost and ::1 by itself. Served on
    127.0.0.2, a request naming another host or origin was answered."""
    given: dict[str, Any] = {}
    monkeypatch.setattr(MCPServer, "run", lambda self, transport, **kw: given.update(kw))
    args = [
        "--http",
        "--host",
        "127.0.0.2",
        "--port",
        "8123",
        "--workspace",
        str(state.workspace.root),
    ]
    assert main(args) == 0
    settings = given["transport_security"]
    app = create_server(state).streamable_http_app(transport_security=settings, host="127.0.0.2")
    requests = [
        ("attacker.example:8123", None),
        ("127.0.0.2:8123", "http://attacker.example"),
        ("127.0.0.2:8123", "http://127.0.0.2:8123"),
        ("127.0.0.2:8123", None),
    ]
    assert http_statuses(app, requests) == [421, 403, 400, 400]


@pytest.mark.parametrize(
    ("host", "port", "allowed", "refused"),
    [
        ("127.0.0.1", 8000, ["127.0.0.1:8000", "localhost:8000", "[::1]:8000"], ["127.0.0.2:8000"]),
        ("::1", 8000, ["[::1]:8000", "localhost:8000"], ["[::2]:8000"]),
        ("127.0.0.2", 80, ["127.0.0.2", "127.0.0.2:80"], ["localhost", "127.0.0.1:80"]),
        ("localhost", 0, ["localhost:51234", "127.0.0.1:51234"], ["example.org:51234"]),
    ],
)
def test_the_allowed_hosts_follow_the_address_served(
    host: str, port: int, allowed: list[str], refused: list[str]
) -> None:
    security = TransportSecurityMiddleware(transport_security(host, port))
    assert all(security._validate_host(name) for name in allowed)
    assert not any(security._validate_host(name) for name in refused)
