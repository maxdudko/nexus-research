"""Public URL checks and connection pinning."""

import asyncio
import gzip
import socket
import time

import httpcore
import httpx
import pytest

from nexus.ai.errors import ResearchError
from nexus.ingestion.transport import (
    fetch_public_page,
    parse_public_url,
    system_resolver,
)

PUBLIC = "93.184.216.34"
HOST = "example.org"


class ChunkStream(httpx.AsyncByteStream):
    """Yield scripted body chunks so the fetcher can stop at a byte cap."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.read = False

    async def __aiter__(self) -> object:
        self.read = True
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


class RecordingTransport(httpx.AsyncBaseTransport):
    """Return scripted responses and record the pinned destination."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("unexpected connection")
        return self._responses.pop(0)

    async def aclose(self) -> None:
        return None


def _response(
    body: bytes,
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
    chunks: list[bytes] | None = None,
) -> httpx.Response:
    stream = ChunkStream(chunks if chunks is not None else [body])
    return httpx.Response(status, headers=headers or _html_headers(), stream=stream)


def _html_headers(**extra: str) -> dict[str, str]:
    headers = {"content-type": "text/html; charset=utf-8"}
    headers.update(extra)
    return headers


def _html(text: str) -> bytes:
    return (
        "<!DOCTYPE html><html><head><title>T</title></head>"
        f"<body><p>{text}</p></body></html>"
    ).encode()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/secret",
        "http://localhost/secret",
        "http://[::1]/secret",
        "http://[fe80::1]/secret",
        "http://[fd00::1]/secret",
        "http://[::ffff:127.0.0.1]/secret",
        "http://10.1.1.1/",
        "http://192.168.0.1/",
        "http://169.254.169.254/",
        "http://user:password@example.org/",
        "file:///etc/passwd",
        "http://2130706433/",
        "http://0177.0.0.1/",
        "http://printer.local/",
        "http://db.internal/admin",
        "gopher://example.org/",
    ],
)
async def test_unsafe_urls_do_not_connect(url: str) -> None:
    connection = RecordingTransport([])
    with pytest.raises(ResearchError) as exc_info:
        await fetch_public_page(
            url,
            max_bytes=1000,
            max_redirects=3,
            timeout_seconds=1,
            connection=connection,
        )
    assert exc_info.value.code == "unsafe_url"
    assert connection.requests == []
    assert "password" not in exc_info.value.message


async def test_rebinding_connects_only_to_the_first_public_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    calls: list[str] = []

    async def resolver(host: str, port: int) -> list[str]:
        calls.append(host)
        if len(calls) == 1:
            return [PUBLIC]
        return ["127.0.0.1"]

    connection = RecordingTransport([_response(_html("Public article"))])
    fetched = await fetch_public_page(
        f"http://{HOST}/article",
        max_bytes=10_000,
        max_redirects=3,
        timeout_seconds=1,
        resolver=resolver,
        connection=connection,
    )

    assert calls == [HOST]
    assert len(connection.requests) == 1
    request = connection.requests[0]
    assert request.url.host == PUBLIC
    assert request.extensions["sni_hostname"] == HOST
    assert request.headers["host"] == HOST
    assert all(item.url.host != "127.0.0.1" for item in connection.requests)
    assert fetched.url == f"http://{HOST}/article"


async def test_redirect_to_a_private_address_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HTTP_PROXY", raising=False)

    async def resolver(host: str, port: int) -> list[str]:
        if host == "internal.example":
            return ["10.1.1.1"]
        return [PUBLIC]

    redirect = httpx.Response(
        302,
        headers={"location": "http://internal.example/secret"},
        stream=ChunkStream([b""]),
    )
    connection = RecordingTransport([redirect, _response(_html("hidden"))])
    with pytest.raises(ResearchError) as exc_info:
        await fetch_public_page(
            f"https://{HOST}/start",
            max_bytes=1000,
            max_redirects=3,
            timeout_seconds=1,
            resolver=resolver,
            connection=connection,
        )
    assert exc_info.value.code == "unsafe_url"
    assert len(connection.requests) == 1
    assert connection.requests[0].url.host == PUBLIC


async def test_redirect_to_a_non_http_scheme_stops() -> None:
    redirect = httpx.Response(
        302,
        headers={"location": "file:///etc/passwd"},
        stream=ChunkStream([b""]),
    )
    connection = RecordingTransport([redirect])

    async def resolver(host: str, port: int) -> list[str]:
        return [PUBLIC]

    with pytest.raises(ResearchError) as exc_info:
        await fetch_public_page(
            f"http://{HOST}/start",
            max_bytes=1000,
            max_redirects=3,
            timeout_seconds=1,
            resolver=resolver,
            connection=connection,
        )
    assert exc_info.value.code == "unsafe_url"
    assert len(connection.requests) == 1


async def test_private_dns_answer_does_not_connect() -> None:
    async def resolver(host: str, port: int) -> list[str]:
        return [PUBLIC, "127.0.0.1"]

    connection = RecordingTransport([_response(_html("no"))])
    with pytest.raises(ResearchError) as exc_info:
        await fetch_public_page(
            f"http://{HOST}/item",
            max_bytes=1000,
            max_redirects=1,
            timeout_seconds=1,
            resolver=resolver,
            connection=connection,
        )
    assert exc_info.value.code == "unsafe_url"
    assert connection.requests == []


async def test_literal_public_address_skips_dns() -> None:
    calls = 0

    async def resolver(host: str, port: int) -> list[str]:
        nonlocal calls
        calls += 1
        return ["127.0.0.1"]

    connection = RecordingTransport([_response(_html("literal"))])
    fetched = await fetch_public_page(
        f"http://{PUBLIC}/article?q=1#frag",
        max_bytes=1000,
        max_redirects=1,
        timeout_seconds=1,
        resolver=resolver,
        connection=connection,
    )
    assert calls == 0
    assert connection.requests[0].url.host == PUBLIC
    assert fetched.url == f"http://{PUBLIC}/article?q=1"


async def test_missing_length_and_oversized_bodies_are_capped() -> None:
    huge = _response(b"", chunks=[b"x" * 50, b"y" * 50], headers=_html_headers())
    connection = RecordingTransport([huge])

    async def resolver(host: str, port: int) -> list[str]:
        return [PUBLIC]

    with pytest.raises(ResearchError) as exc_info:
        await fetch_public_page(
            f"http://{HOST}/big",
            max_bytes=40,
            max_redirects=0,
            timeout_seconds=1,
            resolver=resolver,
            connection=connection,
        )
    assert exc_info.value.code == "payload_too_large"


async def test_declared_length_above_the_cap_is_rejected() -> None:
    stream = ChunkStream([b"tiny"])
    response = httpx.Response(
        200,
        headers=_html_headers(**{"content-length": "99999"}),
        stream=stream,
    )
    connection = RecordingTransport([response])

    async def resolver(host: str, port: int) -> list[str]:
        return [PUBLIC]

    with pytest.raises(ResearchError) as exc_info:
        await fetch_public_page(
            f"http://{HOST}/declared",
            max_bytes=10,
            max_redirects=0,
            timeout_seconds=1,
            resolver=resolver,
            connection=connection,
        )
    assert exc_info.value.code == "payload_too_large"
    assert stream.read is False


async def test_compressed_body_is_capped_after_decompression() -> None:
    payload = gzip.compress(b"z" * 200)
    connection = RecordingTransport(
        [
            _response(
                payload,
                headers=_html_headers(**{"content-encoding": "gzip"}),
            ),
        ],
    )

    async def resolver(host: str, port: int) -> list[str]:
        return [PUBLIC]

    with pytest.raises(ResearchError) as exc_info:
        await fetch_public_page(
            f"http://{HOST}/gzip",
            max_bytes=80,
            max_redirects=0,
            timeout_seconds=1,
            resolver=resolver,
            connection=connection,
        )
    assert exc_info.value.code == "payload_too_large"


async def test_non_html_is_rejected() -> None:
    connection = RecordingTransport(
        [_response(b"plain", headers={"content-type": "text/plain"})],
    )

    async def resolver(host: str, port: int) -> list[str]:
        return [PUBLIC]

    with pytest.raises(ResearchError) as exc_info:
        await fetch_public_page(
            f"http://{HOST}/file",
            max_bytes=1000,
            max_redirects=0,
            timeout_seconds=1,
            resolver=resolver,
            connection=connection,
        )
    assert exc_info.value.code == "fetch_failed"


def test_parse_rejects_before_a_resolver_exists() -> None:
    with pytest.raises(ResearchError) as exc_info:
        parse_public_url("http://127.0.0.1/secret")
    assert exc_info.value.code == "unsafe_url"


class _TlsBackend(httpcore.AsyncNetworkBackend):
    """Record the TCP destination used by the real HTTPX transport."""

    def __init__(self) -> None:
        self.hosts: list[str] = []
        self.tls: list[tuple[str | None, bool | None]] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        **kwargs: object,
    ) -> httpcore.AsyncNetworkStream:
        self.hosts.append(host)
        return _TlsStream(self)

    async def sleep(self, seconds: float) -> None:
        return None


class _TlsStream(httpcore.AsyncNetworkStream):
    """Record the TLS hostname, then stop before a handshake."""

    def __init__(self, backend: _TlsBackend) -> None:
        self._backend = backend

    async def start_tls(
        self,
        ssl_context: object,
        server_hostname: str | None = None,
        **kwargs: object,
    ) -> httpcore.AsyncNetworkStream:
        check_hostname = getattr(ssl_context, "check_hostname", None)
        self._backend.tls.append((server_hostname, check_hostname))
        raise httpcore.ConnectError("stop after tls arguments")

    async def aclose(self) -> None:
        return None


async def test_tls_checks_the_original_hostname() -> None:
    backend = _TlsBackend()
    inner = httpx.AsyncHTTPTransport(trust_env=False)
    inner._pool._network_backend = backend
    lookups: list[str] = []

    async def resolver(host: str, port: int) -> list[str]:
        lookups.append(host)
        if len(lookups) == 1:
            return [PUBLIC]
        return ["127.0.0.1"]

    with pytest.raises(ResearchError) as exc_info:
        await fetch_public_page(
            f"https://{HOST}/article",
            max_bytes=1000,
            max_redirects=1,
            timeout_seconds=1,
            resolver=resolver,
            connection=inner,
        )
    assert exc_info.value.code == "fetch_failed"
    assert backend.hosts == [PUBLIC]
    assert backend.tls == [(HOST, True)]
    assert "127.0.0.1" not in backend.hosts


async def test_empty_dns_does_not_connect() -> None:
    connection = RecordingTransport([_response(_html("hidden"))])

    async def resolver(host: str, port: int) -> list[str]:
        return []

    with pytest.raises(ResearchError) as exc_info:
        await fetch_public_page(
            f"http://{HOST}/item",
            max_bytes=1000,
            max_redirects=0,
            timeout_seconds=1,
            resolver=resolver,
            connection=connection,
        )
    assert exc_info.value.code == "unsafe_url"
    assert connection.requests == []


async def test_system_resolver_hides_dns_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_lookup(*args: object, **kwargs: object) -> list[object]:
        raise socket.gaierror(8, "nodename nor servname provided")

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fail_lookup)
    with pytest.raises(ResearchError) as exc_info:
        await system_resolver(HOST, 443)
    assert exc_info.value.code == "unsafe_url"
    assert HOST not in exc_info.value.message


async def test_redirect_to_loopback_stops_before_a_second_connection() -> None:
    redirect = httpx.Response(
        302,
        headers={"location": "http://127.0.0.1/secret"},
        stream=ChunkStream([b""]),
    )
    connection = RecordingTransport([redirect, _response(_html("hidden"))])

    async def resolver(host: str, port: int) -> list[str]:
        return [PUBLIC]

    with pytest.raises(ResearchError) as exc_info:
        await fetch_public_page(
            f"https://{HOST}/start",
            max_bytes=1000,
            max_redirects=3,
            timeout_seconds=1,
            resolver=resolver,
            connection=connection,
        )
    assert exc_info.value.code == "unsafe_url"
    assert len(connection.requests) == 1
    assert connection.requests[0].url.host == PUBLIC


class _SlowTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        raise AssertionError("the fetch deadline did not cancel the connection")

    async def aclose(self) -> None:
        return None


async def test_fetch_deadline_cancels_a_slow_connection() -> None:
    started = time.monotonic()
    with pytest.raises(ResearchError) as exc_info:
        await fetch_public_page(
            f"http://{PUBLIC}/article",
            max_bytes=1000,
            max_redirects=0,
            timeout_seconds=0.05,
            connection=_SlowTransport(),
        )
    assert exc_info.value.code == "fetch_timeout"
    assert time.monotonic() - started < 2
