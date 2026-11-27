"""The shared HTTP plumbing: throttle, cache, retries, limits and error mapping.

No network: every response comes from an ``httpx2.MockTransport``.
"""

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest

from geoproduct.errors import GeoProductError
from geoproduct.providers.http import (
    CONNECT_TIMEOUT_S,
    EXCERPT_BYTES,
    HttpClient,
    HttpSettings,
    ResponseCache,
    Throttle,
    retry_after_seconds,
)
from geoproduct.schemas import ErrorCode
from geoproduct.workspace import Workspace

Handler = Callable[[httpx2.Request], httpx2.Response]


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(
    handler: Handler,
    tmp_path: Path | None = None,
    *,
    agent: str | None = "test-suite/1.0",
    sleeps: list[float] | None = None,
    **settings: object,
) -> HttpClient:
    config = HttpSettings(
        user_agent=agent, cache_dir=tmp_path / "cache" if tmp_path else None, **settings
    )
    waits = sleeps if sleeps is not None else []
    return HttpClient(
        "example",
        config,
        Throttle(None),
        transport=httpx2.MockTransport(handler),
        sleep=waits.append,
    )


def test_throttle_spaces_requests_by_the_rate_with_a_margin() -> None:
    clock = FakeClock()
    throttle = Throttle(1.0, 1, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        with throttle.slot():
            pass
    assert clock.sleeps == pytest.approx([1.05, 1.05])


def test_throttle_without_a_rate_never_waits() -> None:
    clock = FakeClock()
    throttle = Throttle(None, 2, clock=clock, sleep=clock.sleep)
    for _ in range(5):
        with throttle.slot():
            pass
    assert clock.sleeps == []


def test_a_successful_answer_is_parsed_cached_and_identifies_the_client(
    tmp_path: Path,
) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"ok": True})

    client = make_client(handler, tmp_path)
    first = client.get_json("https://api.example.org/search", {"q": "x"})
    second = client.get_json("https://api.example.org/search", {"q": "x"})
    assert first.data == {"ok": True}
    assert (first.from_cache, first.requests) == (False, 1)
    assert (second.from_cache, second.requests) == (True, 0)
    assert second.retrieved_at == first.retrieved_at
    assert len(seen) == 1
    agent = seen[0].headers["user-agent"]
    assert agent.startswith("test-suite/1.0 geoproduct-core/")
    stored = list((tmp_path / "cache").rglob("*.json"))
    assert len(stored) == 1
    assert "q=x" not in stored[0].read_text(encoding="utf-8")


def test_the_cache_expires(tmp_path: Path) -> None:
    clock = {"now": datetime(2026, 1, 1, tzinfo=UTC)}
    cache = ResponseCache(tmp_path, ttl_s=3600, now=lambda: clock["now"])
    key = ResponseCache.key("GET", "https://x.example/a", {"a": "1"}, {})
    cache.put(key, "https://x.example/a", b'{"v": 1}', clock["now"])
    assert cache.get(key) is not None
    clock["now"] += timedelta(hours=2)
    assert cache.get(key) is None


def test_rate_limits_are_retried_after_the_named_wait() -> None:
    answers = iter(
        [
            httpx2.Response(429, headers={"Retry-After": "3"}),
            httpx2.Response(200, json=[1, 2]),
        ]
    )
    sleeps: list[float] = []
    client = make_client(lambda request: next(answers), sleeps=sleeps)
    result = client.get_json("https://api.example.org/x", {})
    assert result.data == [1, 2]
    assert result.requests == 2
    assert sleeps == [3.0]


def test_a_long_retry_after_is_left_to_the_engine() -> None:
    client = make_client(lambda request: httpx2.Response(429, headers={"Retry-After": "120"}))
    with pytest.raises(GeoProductError) as caught:
        client.get_json("https://api.example.org/x", {})
    error = caught.value.error
    assert error.code == ErrorCode.rate_limited
    assert error.retryable
    assert error.details["retry_after_s"] == 120.0


def test_server_errors_are_retried_then_reported_as_unavailable() -> None:
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(503)

    sleeps: list[float] = []
    client = make_client(handler, sleeps=sleeps)
    with pytest.raises(GeoProductError) as caught:
        client.get_json("https://api.example.org/x", {})
    assert caught.value.code == ErrorCode.provider_unavailable
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]


def test_client_errors_are_not_retried_and_carry_an_excerpt() -> None:
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(400, text="<p>line 1: parse error: unexpected token</p>")

    with pytest.raises(GeoProductError) as caught:
        make_client(handler).post_json("https://api.example.org/q", {"data": "x"})
    error = caught.value.error
    assert error.code == ErrorCode.provider_error
    assert not error.retryable
    assert error.details["excerpt"] == "line 1: parse error: unexpected token"
    assert len(calls) == 1


def test_a_large_error_answer_is_read_only_for_its_excerpt() -> None:
    """Audit GP-07: a 4xx body was read whole, with no size limit. A 1 GiB error page is
    now read only as far as the excerpt needs."""
    sent: list[int] = []

    def body() -> Iterator[bytes]:
        for _ in range(1024 * 1024):
            sent.append(1)
            yield b"<p>" + b"x" * 1017 + b"</p>"

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, content=body())

    with pytest.raises(GeoProductError) as caught:
        make_client(handler).get_json("https://api.example.org/x", {})
    assert caught.value.code == ErrorCode.provider_error
    assert len(caught.value.error.details["excerpt"]) == 300  # type: ignore[arg-type]
    assert len(sent) <= -(-EXCERPT_BYTES // 1024) + 1


def test_a_forbidden_answer_points_at_the_user_agent() -> None:
    client = make_client(lambda request: httpx2.Response(403, text="blocked"))
    with pytest.raises(GeoProductError) as caught:
        client.get_json("https://api.example.org/x", {})
    assert "GEOPRODUCT_USER_AGENT" in (caught.value.error.hint or "")


def test_a_non_json_answer_is_a_provider_error() -> None:
    client = make_client(lambda request: httpx2.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(GeoProductError) as caught:
        client.get_json("https://api.example.org/x", {})
    assert caught.value.code == ErrorCode.provider_error


def test_oversized_answers_are_refused() -> None:
    body = json.dumps({"items": ["x" * 100] * 50}).encode()
    client = make_client(lambda request: httpx2.Response(200, content=body), max_bytes=1000)
    with pytest.raises(GeoProductError) as caught:
        client.get_json("https://api.example.org/x", {})
    assert caught.value.code == ErrorCode.too_large


def test_timeouts_are_retryable_timeouts() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    client = make_client(handler, max_retries=0)
    with pytest.raises(GeoProductError) as caught:
        client.get_json("https://api.example.org/x", {})
    assert caught.value.code == ErrorCode.timeout
    assert caught.value.error.retryable


def test_connection_failures_are_unavailable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    client = make_client(handler, max_retries=0)
    with pytest.raises(GeoProductError) as caught:
        client.get_json("https://api.example.org/x", {})
    assert caught.value.code == ErrorCode.provider_unavailable


def test_a_host_that_never_accepts_the_connection_is_unavailable_not_slow() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectTimeout("no answer to the connection", request=request)

    client = make_client(handler, max_retries=0)
    with pytest.raises(GeoProductError) as caught:
        client.post_json("https://api.example.org/x", {"q": "1"}, timeout_s=120)
    assert caught.value.code == ErrorCode.provider_unavailable
    assert caught.value.error.retryable
    assert "no connection within 10 s" in caught.value.error.message
    assert "narrow the query" not in (caught.value.error.hint or "")


def test_connecting_has_a_short_limit_of_its_own() -> None:
    limits: list[dict[str, float]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        limits.append(request.extensions["timeout"])
        return httpx2.Response(200, json={})

    client = make_client(handler)
    client.get_json("https://api.example.org/a", {})
    client.post_json("https://api.example.org/b", {"q": "1"}, timeout_s=120)
    client.post_json("https://api.example.org/c", {"q": "1"}, timeout_s=5)
    assert [(limit["connect"], limit["read"]) for limit in limits] == [
        (CONNECT_TIMEOUT_S, 30),
        (CONNECT_TIMEOUT_S, 120),
        (5, 5),  # never longer than the whole request may take
    ]


def test_retry_after_accepts_seconds_and_dates() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert retry_after_seconds("7", now) == 7.0
    assert retry_after_seconds("Thu, 01 Jan 2026 12:00:30 GMT", now) == 30.0
    assert retry_after_seconds("soon", now) is None
    assert retry_after_seconds(None, now) is None


def test_settings_come_from_the_environment(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path).ensure()
    settings = HttpSettings.from_env(
        {"GEOPRODUCT_USER_AGENT": "my-app/2.0 (ops@example.org)"}, workspace
    )
    assert settings.user_agent == "my-app/2.0 (ops@example.org)"
    assert settings.cache_dir == workspace.root / "cache" / "http"
    assert HttpSettings.from_env({"GEOPRODUCT_CACHE_TTL_HOURS": "0"}, workspace).cache_dir is None
    assert HttpSettings.from_env({}, workspace).user_agent is None


@pytest.mark.parametrize(
    "env",
    [
        {"GEOPRODUCT_USER_AGENT": "x"},
        {"GEOPRODUCT_USER_AGENT": "bad\nheader: injected"},
        {"GEOPRODUCT_CACHE_TTL_HOURS": "-1"},
        {"GEOPRODUCT_CACHE_TTL_HOURS": "soon"},
    ],
)
def test_invalid_settings_are_refused(env: dict[str, str]) -> None:
    with pytest.raises(GeoProductError) as caught:
        HttpSettings.from_env(env, None)
    assert caught.value.code == ErrorCode.invalid_input
