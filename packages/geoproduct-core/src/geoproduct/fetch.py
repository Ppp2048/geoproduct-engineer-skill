"""Fetching one source document, safely and politely.

Used to keep a copy of a source the agent cites, so its content hash can be recorded and
evidence excerpts can be checked against its text. It is not a crawler: one URL per call,
no link following beyond redirects.

Safety rules:

- only ``http`` and ``https`` URLs, without user names or passwords in them;
- only public addresses. The check runs when the connection is made, on the address
  actually used, so a host name that resolves to a private, loopback, link-local or
  otherwise reserved address is refused, including through DNS rebinding or a redirect;
- proxies from the environment are ignored, so the check cannot be bypassed through one;
- an allowlist of content types, a size cap, a time limit and at most five redirects;
- ``robots.txt`` is respected for the configured User-Agent.
"""

import hashlib
import ipaddress
import re
import socket
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, Final
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpcore2
import httpx2

from geoproduct.errors import GeoProductError
from geoproduct.schemas.errors import ErrorCode

MAX_FETCH_BYTES: Final = 20 * 1024 * 1024
MAX_REDIRECTS: Final = 5
TIMEOUT_S: Final = 30.0
MAX_ROBOTS_BYTES: Final = 512 * 1024

CONTENT_TYPES: Final = {
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/plain": "text",
    "text/markdown": "text",
    "text/csv": "csv",
    "application/csv": "csv",
    "application/json": "json",
    "application/geo+json": "geojson",
    "application/vnd.geo+json": "geojson",
    "application/xml": "xml",
    "text/xml": "xml",
    "application/pdf": "pdf",
}
"""Accepted content types and the kind each is stored as."""

SUFFIX_KINDS: Final = {
    ".csv": "csv",
    ".geojson": "geojson",
    ".json": "json",
    ".txt": "text",
    ".md": "text",
    ".html": "html",
    ".htm": "html",
    ".xml": "xml",
    ".pdf": "pdf",
}
"""Used when a server sends a generic type such as ``application/octet-stream``."""

GENERIC_TYPES: Final = frozenset({"application/octet-stream", "binary/octet-stream", ""})

CARRIES_IPV4: Final = (
    ipaddress.IPv6Network("64:ff9b::/96"),  # NAT64, well-known prefix (RFC 6052)
    ipaddress.IPv6Network("::/96"),  # IPv4-compatible (deprecated by RFC 4291)
)
"""IPv6 ranges whose last 32 bits are an IPv4 address that the packets may reach."""


@dataclass(frozen=True)
class Fetched:
    url: str
    """The URL fetched, after redirects."""
    content_type: str
    kind: str
    body: bytes
    sha256: str
    retrieved_at: datetime
    text: str | None
    """Plain text for checking excerpts; ``None`` for PDFs, whose text is not extracted."""


@dataclass(frozen=True)
class _Page:
    status: int
    headers: httpx2.Headers
    body: bytes

    @property
    def location(self) -> str | None:
        if self.status in (301, 302, 303, 307, 308):
            return self.headers.get("location")
        return None

    @property
    def charset(self) -> str | None:
        match = re.search(r"charset=([\w.:-]+)", self.headers.get("content-type", ""), re.I)
        return match.group(1) if match else None


def _refused(message: str, url: str, hint: str | None = None) -> GeoProductError:
    return GeoProductError(ErrorCode.policy_refused, message, details={"url": url[:500]}, hint=hint)


def is_public(address: str) -> bool:
    """Whether an address is global. An IPv6 address that carries an IPv4 one is judged by
    the IPv4 address: ``ipaddress`` calls ``64:ff9b::7f00:1`` global, but through a NAT64
    gateway it reaches 127.0.0.1 (audit GP-10)."""
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif any(ip in network for network in CARRIES_IPV4):
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip.is_global and not ip.is_multicast


class _PublicOnlyBackend(httpcore2.NetworkBackend):
    """Resolves the host itself and connects only to a public address it checked."""

    def __init__(self) -> None:
        self._inner = httpcore2.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore2.NetworkStream:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            raise httpcore2.ConnectError(f"cannot resolve {host}") from None
        addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
        if not addresses or not all(is_public(a) for a in addresses):
            raise _refused(
                "The URL points to a private, local or reserved network address.",
                host,
                hint="Only public web addresses can be fetched.",
            )
        return self._inner.connect_tcp(addresses[0], port, timeout, local_address, socket_options)

    def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Iterable[Any] | None = None
    ) -> httpcore2.NetworkStream:
        raise _refused("Unix sockets cannot be fetched.", path)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class _GuardedTransport(httpx2.HTTPTransport):
    def __init__(self) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore2.ConnectionPool(
            ssl_context=httpx2.create_ssl_context(trust_env=False),
            max_connections=4,
            network_backend=_PublicOnlyBackend(),
        )


def check_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise _refused("Only http and https URLs can be fetched.", url)
    if parts.username or parts.password:
        raise _refused(
            "URLs with a user name or password are not fetched.",
            url,
            hint="Remove the credentials; sources must be publicly readable.",
        )
    return url


class _Text(HTMLParser):
    SKIP: Final = frozenset({"script", "style", "noscript", "template", "svg"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skipping += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skipping:
            self._skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def plain_text(body: bytes, kind: str, charset: str | None = None) -> str | None:
    """Text for excerpt checks: markup removed and whitespace collapsed."""
    if kind == "pdf":
        return None
    text = body.decode(charset or "utf-8", errors="replace")
    if kind in ("html", "xml"):
        parser = _Text()
        parser.feed(text)
        parser.close()
        text = " ".join(parser.parts)
    return " ".join(text.split())


def normalise_for_match(text: str) -> str:
    """Case, quotes, dashes and whitespace folded, so a faithful excerpt still matches."""
    text = text.casefold()
    text = re.sub("[\u2018\u2019\u201b\u2032`]", "'", text)
    text = re.sub("[\u201c\u201d\u201e\u2033]", '"', text)
    text = re.sub("[\u2010-\u2015\u2212]", "-", text)
    return " ".join(text.split())


def _kind(content_type: str, url: str) -> str | None:
    if content_type in CONTENT_TYPES:
        return CONTENT_TYPES[content_type]
    if content_type in GENERIC_TYPES:
        path = urlsplit(url).path.lower()
        return next((k for s, k in SUFFIX_KINDS.items() if path.endswith(s)), None)
    return None


class Fetcher:
    def __init__(
        self,
        user_agent: str,
        *,
        max_bytes: int = MAX_FETCH_BYTES,
        timeout_s: float = TIMEOUT_S,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.max_bytes = max_bytes
        self.timeout_s = timeout_s
        self._transport = transport

    def fetch(self, url: str, *, respect_robots: bool = True) -> Fetched:
        check_url(url)
        with httpx2.Client(
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout_s,
            follow_redirects=False,
            transport=self._transport or _GuardedTransport(),
            trust_env=False,
        ) as client:
            if respect_robots:
                self._check_robots(client, url)
            for _ in range(MAX_REDIRECTS + 1):
                page = self._get(client, url, self.max_bytes)
                if location := page.location:
                    url = check_url(urljoin(url, location))
                    if respect_robots:
                        self._check_robots(client, url)
                    continue
                return self._result(url, page)
        raise _refused(f"More than {MAX_REDIRECTS} redirects.", url)

    def _get(self, client: httpx2.Client, url: str, limit: int) -> _Page:
        try:
            with client.stream("GET", url) as response:
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > limit:
                    raise self._too_large(url, limit)
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise self._too_large(url, limit)
                    chunks.append(chunk)
                return _Page(response.status_code, response.headers, b"".join(chunks))
        except httpx2.TimeoutException:
            raise GeoProductError(
                ErrorCode.timeout,
                f"The server did not answer within {self.timeout_s:g} s.",
                details={"url": url[:500]},
            ) from None
        except httpx2.TransportError as exc:
            raise GeoProductError(
                ErrorCode.provider_unavailable,
                f"The URL could not be fetched ({type(exc).__name__}).",
                details={"url": url[:500]},
                hint="Check the address, or try again later.",
            ) from None

    def _result(self, url: str, page: _Page) -> Fetched:
        if page.status != 200:
            code = ErrorCode.not_found if page.status in (404, 410) else ErrorCode.provider_error
            raise GeoProductError(
                code,
                f"The server answered with HTTP {page.status}.",
                details={"url": url[:500], "status": page.status},
            )
        header = page.headers.get("content-type", "")
        content_type = header.split(";", 1)[0].strip().lower()
        kind = _kind(content_type, url)
        if kind is None:
            raise _refused(
                f"Content type '{content_type or 'none'}' is not accepted.",
                url,
                hint="Accepted: HTML, plain text, CSV, JSON, GeoJSON, XML and PDF.",
            )
        body = page.body
        return Fetched(
            url=url,
            content_type=content_type or "application/octet-stream",
            kind=kind,
            body=body,
            sha256=hashlib.sha256(body).hexdigest(),
            retrieved_at=datetime.now(UTC),
            text=plain_text(body, kind, page.charset),
        )

    def _check_robots(self, client: httpx2.Client, url: str) -> None:
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        try:
            page = self._get(client, robots_url, MAX_ROBOTS_BYTES)
        except GeoProductError as exc:
            if exc.code == ErrorCode.policy_refused:
                raise
            return
        if page.status in (401, 403):
            raise _refused("The site's robots.txt cannot be read; it forbids access.", url)
        if page.status != 200:
            return
        parser = RobotFileParser()
        parser.parse(page.body.decode("utf-8", errors="replace").splitlines())
        if not parser.can_fetch(self.user_agent, url):
            raise _refused(
                "The site's robots.txt does not allow fetching this URL.",
                url,
                hint="Cite the source without fetching it; its excerpts stay unchecked.",
            )

    @staticmethod
    def _too_large(url: str, limit: int) -> GeoProductError:
        return GeoProductError(
            ErrorCode.too_large,
            f"The document is larger than {limit // (1024 * 1024) or 1} MB.",
            details={"url": url[:500], "limit_bytes": limit},
        )
