"""Fetching source documents: address checks, robots.txt, content types and limits.

The address checks run without a network: refusing a private address happens before any
connection is attempted. Everything else uses a mock transport.
"""

import httpx2
import pytest

from geoproduct.errors import GeoProductError
from geoproduct.fetch import (
    Fetcher,
    _PublicOnlyBackend,
    check_url,
    is_public,
    normalise_for_match,
    plain_text,
)
from geoproduct.schemas import ErrorCode

PAGE = (
    "<html><head><style>p {color: red}</style><script>var x = 1;</script></head>"
    "<body><h1>Example Clinic</h1><p>The clinic has 12&nbsp;beds.</p></body></html>"
)


def fetcher(routes: dict[str, httpx2.Response]) -> Fetcher:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return routes.get(str(request.url), httpx2.Response(404))

    return Fetcher("test-suite/1.0", transport=httpx2.MockTransport(handler), max_bytes=10_000)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "0.0.0.0",
        "224.0.0.1",
    ],
)
def test_private_and_special_addresses_are_not_public(address: str) -> None:
    assert not is_public(address)


@pytest.mark.parametrize("address", ["93.184.215.14", "2606:4700::1111"])
def test_global_addresses_are_public(address: str) -> None:
    assert is_public(address)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "10.0.0.1"])
def test_connections_to_private_hosts_are_refused_before_connecting(host: str) -> None:
    with pytest.raises(GeoProductError) as caught:
        _PublicOnlyBackend().connect_tcp(host, 80, timeout=1)
    assert caught.value.code == ErrorCode.policy_refused


def test_the_real_transport_refuses_a_loopback_url() -> None:
    with pytest.raises(GeoProductError) as caught:
        Fetcher("test-suite/1.0").fetch("http://127.0.0.1:9/page", respect_robots=False)
    assert caught.value.code == ErrorCode.policy_refused


@pytest.mark.parametrize(
    "url",
    [
        "ftp://files.example.org/a.csv",
        "file:///etc/passwd",
        "https://user:secret@example.org/",
        "http:///nohost",
    ],
)
def test_only_plain_http_urls_are_accepted(url: str) -> None:
    with pytest.raises(GeoProductError) as caught:
        check_url(url)
    assert caught.value.code == ErrorCode.policy_refused


def test_html_is_fetched_hashed_and_turned_into_text() -> None:
    page = httpx2.Response(200, text=PAGE, headers={"content-type": "text/html; charset=utf-8"})
    result = fetcher({"https://example.org/clinic": page}).fetch("https://example.org/clinic")
    assert result.kind == "html"
    assert result.text == "Example Clinic The clinic has 12 beds."
    assert len(result.sha256) == 64


def test_robots_txt_is_respected() -> None:
    robots = httpx2.Response(200, text="User-agent: *\nDisallow: /private/\n")
    page = httpx2.Response(200, text="hi", headers={"content-type": "text/plain"})
    routes = {
        "https://example.org/robots.txt": robots,
        "https://example.org/private/a": page,
        "https://example.org/public/a": page,
    }
    assert fetcher(routes).fetch("https://example.org/public/a").text == "hi"
    with pytest.raises(GeoProductError) as caught:
        fetcher(routes).fetch("https://example.org/private/a")
    assert caught.value.code == ErrorCode.policy_refused


def test_redirects_are_followed_and_rechecked() -> None:
    routes = {
        "https://example.org/old": httpx2.Response(301, headers={"location": "/new.csv"}),
        "https://example.org/new.csv": httpx2.Response(
            200, text="id,name\n1,A\n", headers={"content-type": "application/octet-stream"}
        ),
        "https://example.org/away": httpx2.Response(302, headers={"location": "ftp://x/y"}),
    }
    result = fetcher(routes).fetch("https://example.org/old")
    assert result.url == "https://example.org/new.csv"
    assert result.kind == "csv"
    with pytest.raises(GeoProductError):
        fetcher(routes).fetch("https://example.org/away")


def test_unaccepted_types_missing_pages_and_large_files_are_errors() -> None:
    routes = {
        "https://example.org/app.exe": httpx2.Response(
            200, content=b"MZ", headers={"content-type": "application/x-msdownload"}
        ),
        "https://example.org/big.txt": httpx2.Response(
            200, text="x" * 20_000, headers={"content-type": "text/plain"}
        ),
    }
    with pytest.raises(GeoProductError) as caught:
        fetcher(routes).fetch("https://example.org/app.exe")
    assert caught.value.code == ErrorCode.policy_refused
    with pytest.raises(GeoProductError) as caught:
        fetcher(routes).fetch("https://example.org/missing")
    assert caught.value.code == ErrorCode.not_found
    with pytest.raises(GeoProductError) as caught:
        fetcher(routes).fetch("https://example.org/big.txt")
    assert caught.value.code == ErrorCode.too_large


def test_pdf_text_is_not_extracted() -> None:
    assert plain_text(b"%PDF-1.7", "pdf") is None


def test_excerpts_match_despite_quotes_case_and_spacing() -> None:
    page = normalise_for_match("The clinic’s  “west wing” opened in 2019 – slowly.")  # noqa: RUF001 (the curly quotes and dash are the point of the test)
    assert normalise_for_match('the clinic\'s "WEST wing" opened') in page
    assert normalise_for_match("opened in 2019 - slowly") in page
