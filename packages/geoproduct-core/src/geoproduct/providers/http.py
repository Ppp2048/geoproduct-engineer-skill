"""Shared HTTP plumbing for providers that call web services.

Every live provider gets the same behaviour:

- an identifying ``User-Agent`` built from ``GEOPRODUCT_USER_AGENT`` (the public OpenStreetMap
  services refuse anonymous clients);
- a throttle that spaces requests and limits how many run at once, per provider, across all
  threads of this process;
- an on-disk cache of successful responses in the workspace, so repeated queries cost nothing
  and a resumed run does not query again. A provider can pass a ``check`` that refuses an
  answer (one a service marked as partial, for example); a refused answer is never cached,
  and a cached one the check refuses is dropped and asked for again;
- size and time limits on every response;
- a few short retries on transient failures, and otherwise an error with a stable code
  (``rate_limited``, ``provider_unavailable``, ``timeout``, ``provider_error``, ``too_large``)
  that carries ``retry_after_s`` when the service named one, so the engine can back off.

The throttle is per process: two processes using one public endpoint each keep its rate, so
together they can exceed it. Run one server or one pipeline at a time against public services.
"""

import email.utils
import hashlib
import json
import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

import httpx2

from geoproduct import __version__
from geoproduct.errors import GeoProductError
from geoproduct.schemas.errors import ErrorCode
from geoproduct.workspace import Workspace

ENV_USER_AGENT: Final = "GEOPRODUCT_USER_AGENT"
ENV_CACHE_TTL_HOURS: Final = "GEOPRODUCT_CACHE_TTL_HOURS"
DEFAULT_CACHE_TTL_HOURS: Final = 24 * 7
MAX_RESPONSE_BYTES: Final = 50 * 1024 * 1024
EXCERPT_BYTES: Final = 4000
"""How much of an error answer is read: enough for its excerpt, never the whole body."""
CONNECT_TIMEOUT_S: Final = 10.0
"""How long to wait for a connection. An answer may take much longer (an Overpass query
runs for minutes), but a host that has not accepted the connection by now is unreachable."""
SPACING_MARGIN: Final = 1.05
"""Requests are spaced 5% further apart than a policy's rate, to stay clear of its limit."""

Check = Callable[[Any], GeoProductError | None]
"""Checks a parsed answer before it is used or cached: the error if it must be refused."""

_USER_AGENT = re.compile(r"^[\x20-\x7e]{3,200}$")
_TAGS = re.compile(r"<[^>]+>")


def endpoint_from_env(env: Mapping[str, str], variable: str, default: str) -> str:
    """A provider endpoint from the environment, or ``default`` when it is not set.

    The URL is shown in the providers resource and recorded in run manifests, so it may not
    carry a user name, password, query or fragment, where a key could hide. The value is
    never repeated in the error.
    """
    text = env.get(variable, "").strip()
    if not text:
        return default
    try:
        parts = urlsplit(text)
        valid = (
            parts.scheme in ("http", "https")
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
            and not parts.query
            and not parts.fragment
        )
        _ = parts.port  # raises ValueError for a malformed port
    except ValueError:
        valid = False
    if not valid:
        raise GeoProductError(
            ErrorCode.invalid_input,
            f"{variable} must be an http or https URL with a host, and no user name, "
            "password, query or fragment.",
            hint="Keep credentials out of endpoint URLs; GeoProduct never sends any.",
        )
    return text.rstrip("/")


def same_host(url: str, other: str) -> bool:
    """Whether two URLs name the same host, ignoring case, scheme, port and path."""
    return urlsplit(url).hostname == urlsplit(other).hostname


@dataclass(frozen=True)
class HttpSettings:
    user_agent: str | None = None
    """Identifies the application to the services; ``None`` when not configured."""
    cache_dir: Path | None = None
    """Where responses are cached; ``None`` disables the cache."""
    cache_ttl_s: float = DEFAULT_CACHE_TTL_HOURS * 3600.0
    timeout_s: float = 30.0
    max_bytes: int = MAX_RESPONSE_BYTES
    max_retries: int = 2
    max_retry_wait_s: float = 10.0
    """Longer waits are left to the caller (the engine retries stages with backoff)."""

    @classmethod
    def from_env(cls, env: Mapping[str, str], workspace: Workspace | None) -> "HttpSettings":
        agent = env.get(ENV_USER_AGENT, "").strip() or None
        if agent is not None and not _USER_AGENT.match(agent):
            raise GeoProductError(
                ErrorCode.invalid_input,
                f"{ENV_USER_AGENT} must be 3 to 200 printable ASCII characters.",
                hint="For example: 'my-project/1.0 (contact: you@example.org)'.",
            )
        ttl_hours = float(DEFAULT_CACHE_TTL_HOURS)
        if text := env.get(ENV_CACHE_TTL_HOURS, "").strip():
            try:
                ttl_hours = float(text)
            except ValueError:
                ttl_hours = -1.0
            if not 0 <= ttl_hours <= 24 * 365:
                raise GeoProductError(
                    ErrorCode.invalid_input,
                    f"{ENV_CACHE_TTL_HOURS} must be a number of hours from 0 to 8760.",
                    hint="Use 0 to turn the response cache off.",
                )
        cache_dir = None
        if workspace is not None and ttl_hours > 0:
            cache_dir = workspace.path("cache", "http")
        return cls(user_agent=agent, cache_dir=cache_dir, cache_ttl_s=ttl_hours * 3600.0)

    def header(self) -> str | None:
        if self.user_agent is None:
            return None
        return f"{self.user_agent} geoproduct-core/{__version__}"


class Throttle:
    """Starts requests at least ``SPACING_MARGIN / rate`` seconds apart, and runs at most
    ``concurrency`` at once."""

    def __init__(
        self,
        rate: float | None,
        concurrency: int = 1,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.interval = SPACING_MARGIN / rate if rate else 0.0
        self._slots = threading.BoundedSemaphore(concurrency)
        self._lock = threading.Lock()
        self._next: float | None = None
        self._clock = clock
        self._sleep = sleep

    @contextmanager
    def slot(self) -> Iterator[None]:
        with self._slots:
            with self._lock:
                now = self._clock()
                start = now if self._next is None else max(now, self._next)
                self._next = start + self.interval
            if start > now:
                self._sleep(start - now)
            yield


@dataclass(frozen=True)
class CachedBody:
    body: bytes
    retrieved_at: datetime


class ResponseCache:
    """Successful responses on disk, keyed by a hash of the request.

    Only the request's URL path is stored in the clear. Query values are part of the hash,
    never of a file name.
    """

    def __init__(
        self, folder: Path, ttl_s: float, *, now: Callable[[], datetime] | None = None
    ) -> None:
        self.folder = folder
        self.ttl_s = ttl_s
        self._now = now or (lambda: datetime.now(UTC))

    @staticmethod
    def key(method: str, url: str, params: Mapping[str, str], form: Mapping[str, str]) -> str:
        text = json.dumps(
            [method, url, sorted(params.items()), sorted(form.items())], ensure_ascii=False
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.folder / key[:2] / f"{key}.json"

    def get(self, key: str) -> CachedBody | None:
        path = self._path(key)
        try:
            entry = Workspace.read_json(path)
            retrieved_at = datetime.fromisoformat(entry["retrieved_at"])
            body = str(entry["body"]).encode("utf-8")
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if (self._now() - retrieved_at).total_seconds() > self.ttl_s:
            return None
        return CachedBody(body, retrieved_at)

    def drop(self, key: str) -> None:
        """Forget one response, e.g. one a provider no longer accepts. If the file cannot be
        removed now, the next successful answer replaces it."""
        with suppress(OSError):
            self._path(key).unlink(missing_ok=True)

    def put(self, key: str, url: str, body: bytes, retrieved_at: datetime) -> None:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return
        entry = {"url": url, "retrieved_at": retrieved_at.isoformat(), "body": text}
        data = json.dumps(entry, ensure_ascii=False).encode("utf-8")
        Workspace.write_bytes_atomic(self._path(key), data)


@dataclass(frozen=True)
class HttpResult:
    data: Any
    """The parsed JSON body."""
    retrieved_at: datetime
    from_cache: bool
    requests: int
    """HTTP requests made for this result: 0 from the cache, more than 1 after retries."""


def retry_after_seconds(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds to wait from a ``Retry-After`` header: a number of seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


def _head(response: httpx2.Response, limit: int) -> bytes:
    """At most ``limit`` bytes of a streamed body. The rest is never read: closing the
    response drops it with the connection."""
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= limit:
            break
    return b"".join(chunks)[:limit]


def _excerpt(body: bytes, limit: int = 300) -> str:
    text = _TAGS.sub(" ", body[:EXCERPT_BYTES].decode("utf-8", errors="replace"))
    return " ".join(text.split())[:limit]


class HttpClient:
    """JSON requests to one provider's service, with its throttle, cache and limits."""

    def __init__(
        self,
        provider: str,
        settings: HttpSettings,
        throttle: Throttle,
        *,
        transport: httpx2.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings
        self.throttle = throttle
        self._transport = transport
        self._sleep = sleep
        self._now = now or (lambda: datetime.now(UTC))
        self._cache = (
            ResponseCache(settings.cache_dir / provider, settings.cache_ttl_s, now=self._now)
            if settings.cache_dir is not None
            else None
        )
        self._client: httpx2.Client | None = None
        self._client_lock = threading.Lock()

    def _http(self) -> httpx2.Client:
        with self._client_lock:
            if self._client is None:
                headers = {"Accept": "application/json"}
                if agent := self.settings.header():
                    headers["User-Agent"] = agent
                self._client = httpx2.Client(
                    headers=headers,
                    timeout=self.settings.timeout_s,
                    follow_redirects=False,
                    transport=self._transport,
                )
            return self._client

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def get_json(
        self, url: str, params: Mapping[str, str], *, check: Check | None = None
    ) -> HttpResult:
        return self._request("GET", url, params, {}, None, check)

    def post_json(
        self,
        url: str,
        form: Mapping[str, str],
        *,
        timeout_s: float | None = None,
        check: Check | None = None,
    ) -> HttpResult:
        return self._request("POST", url, {}, form, timeout_s, check)

    def _request(
        self,
        method: str,
        url: str,
        params: Mapping[str, str],
        form: Mapping[str, str],
        timeout_s: float | None,
        check: Check | None,
    ) -> HttpResult:
        key = ResponseCache.key(method, url, params, form)
        if self._cache is not None and (hit := self._cache.get(key)) is not None:
            data = self._parse(hit.body)
            if check is None or check(data) is None:
                return HttpResult(data, hit.retrieved_at, True, 0)
            # Cached before it was checked (by an earlier version): ask the service again.
            self._cache.drop(key)
        attempt = 0
        while True:
            attempt += 1
            try:
                body = self._send(method, url, params, form, timeout_s)
                break
            except GeoProductError as exc:
                wait = self._retry_wait(exc, attempt)
                if wait is None:
                    raise
                self._sleep(wait)
        data = self._parse(body)
        if check is not None and (refused := check(data)) is not None:
            raise refused
        retrieved_at = self._now()
        if self._cache is not None:
            self._cache.put(key, url, body, retrieved_at)
        return HttpResult(data, retrieved_at, False, attempt)

    def _retry_wait(self, exc: GeoProductError, attempt: int) -> float | None:
        if not exc.error.retryable or attempt > self.settings.max_retries:
            return None
        after = exc.error.details.get("retry_after_s")
        wait = float(after) if isinstance(after, int | float) else 2.0 ** (attempt - 1)
        return wait if wait <= self.settings.max_retry_wait_s else None

    def _send(
        self,
        method: str,
        url: str,
        params: Mapping[str, str],
        form: Mapping[str, str],
        timeout_s: float | None,
    ) -> bytes:
        timeout = timeout_s if timeout_s is not None else self.settings.timeout_s
        connect = min(CONNECT_TIMEOUT_S, timeout)
        with self.throttle.slot():
            try:
                with self._http().stream(
                    method,
                    url,
                    params=dict(params) or None,
                    data=dict(form) or None,
                    timeout=httpx2.Timeout(timeout, connect=connect),
                ) as response:
                    if response.status_code != 200:
                        raise self._status_error(response.status_code, response)
                    return self._read(response)
            except httpx2.ConnectTimeout:
                # Not a slow answer: nothing answered at all, so narrowing the query won't help.
                raise self._error(
                    ErrorCode.provider_unavailable,
                    f"Provider '{self.provider}' could not be reached: no connection within "
                    f"{connect:g} s.",
                    hint="Check the network and the endpoint setting, then try again. Some "
                    "networks block these services; another instance can be set in the "
                    "provider's URL variable.",
                ) from None
            except httpx2.TimeoutException:
                raise self._error(
                    ErrorCode.timeout,
                    f"Provider '{self.provider}' did not answer within {timeout:g} s.",
                    hint="Try again later, or narrow the query.",
                ) from None
            except httpx2.TransportError as exc:
                raise self._error(
                    ErrorCode.provider_unavailable,
                    f"Provider '{self.provider}' could not be reached ({type(exc).__name__}).",
                    hint="Check the network and the endpoint setting, then try again.",
                ) from None

    def _read(self, response: httpx2.Response) -> bytes:
        limit = self.settings.max_bytes
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise self._too_large(limit)
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > limit:
                raise self._too_large(limit)
            chunks.append(chunk)
        return b"".join(chunks)

    def _status_error(self, status: int, response: httpx2.Response) -> GeoProductError:
        retry_after = retry_after_seconds(response.headers.get("retry-after"), self._now())
        details: dict[str, object] = {"status": status}
        if retry_after is not None:
            details["retry_after_s"] = round(retry_after, 1)
        if status == 429:
            return self._error(
                ErrorCode.rate_limited,
                f"Provider '{self.provider}' is rate limiting requests.",
                hint="Wait before trying again; the engine backs off automatically.",
                **details,
            )
        if status >= 500:
            return self._error(
                ErrorCode.provider_unavailable,
                f"Provider '{self.provider}' answered with HTTP {status}.",
                hint="The service may be busy or down; try again later.",
                **details,
            )
        with suppress(httpx2.HTTPError):
            details["excerpt"] = _excerpt(_head(response, EXCERPT_BYTES))
        hint = "Check the query parameters."
        if status in (401, 403):
            hint = (
                f"The service refused the request. Check {ENV_USER_AGENT} and the "
                "provider's usage policy."
            )
        return self._error(
            ErrorCode.provider_error,
            f"Provider '{self.provider}' rejected the request with HTTP {status}.",
            hint=hint,
            **details,
        )

    def _parse(self, body: bytes) -> Any:
        try:
            return json.loads(body)
        except ValueError:
            raise self._error(
                ErrorCode.provider_error,
                f"Provider '{self.provider}' returned a response that is not JSON.",
                excerpt=_excerpt(body),
            ) from None

    def _too_large(self, limit: int) -> GeoProductError:
        return self._error(
            ErrorCode.too_large,
            f"The response from '{self.provider}' is larger than {limit // (1024 * 1024)} MB.",
            hint="Query a smaller area or fewer tags.",
            limit_bytes=limit,
        )

    def _error(
        self, code: ErrorCode, message: str, *, hint: str | None = None, **details: object
    ) -> GeoProductError:
        return GeoProductError(
            code, message, details={"provider": self.provider, **details}, hint=hint
        )


ClientFactory = Callable[[str, Throttle], HttpClient]
"""Makes the HTTP client for a provider, given the provider's name and throttle."""
