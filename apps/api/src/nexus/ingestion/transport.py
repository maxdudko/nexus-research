"""SSRF-safe public page fetch.

Connections go to one address checked before the socket opens. The request URL
host is rewritten to that address and ``sni_hostname`` keeps the original name,
so a later DNS answer cannot move the connection. Redirects repeat the same
checks. ``trust_env`` stays off, so proxy variables cannot choose a destination.
"""

import asyncio
import ipaddress
import logging
import socket
import zlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from nexus.ai.errors import fetch_failed, fetch_timeout, payload_too_large, unsafe_url

logger = logging.getLogger(__name__)

AddressResolver = Callable[[str, int], Awaitable[Sequence[str]]]
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_BLOCKED_SUFFIXES = (".local", ".localhost", ".internal")


@dataclass(frozen=True)
class FetchedPage:
    """Bounded HTML and the public URL it was fetched from."""

    url: str
    body: bytes


async def system_resolver(host: str, port: int) -> list[str]:
    """Resolve a hostname with the system resolver."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        raise unsafe_url() from None
    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise unsafe_url()
    return addresses


def parse_public_url(value: str) -> httpx.URL:
    """Reject credentials, non-HTTP schemes, and non-public address literals."""
    try:
        url = httpx.URL(value.strip())
    except httpx.InvalidURL, UnicodeError, ValueError:
        raise unsafe_url() from None
    if url.scheme not in {"http", "https"} or url.userinfo or not url.host:
        raise unsafe_url()
    host = url.host.lower().rstrip(".")
    if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
        raise unsafe_url()
    if host.isdigit() or _ambiguous_numeric_host(host):
        raise unsafe_url()
    if _literal_ip(host) is not None and not _is_public(host):
        raise unsafe_url()
    return url


def canonical_url(url: httpx.URL) -> str:
    """Return the fetch URL without userinfo, fragment, or a default port."""
    host = url.host.lower()
    host_text = f"[{host}]" if ":" in host else host
    netloc = host_text if _explicit_port(url) is None else f"{host_text}:{url.port}"
    path = url.path or "/"
    raw_query = url.query
    query = (
        raw_query.decode("ascii", errors="ignore")
        if isinstance(raw_query, bytes)
        else raw_query
    )
    rendered = f"{url.scheme}://{netloc}{path}"
    if query:
        rendered = f"{rendered}?{query}"
    return rendered


class PinnedTransport(httpx.AsyncBaseTransport):
    """Connect to a validated address and keep the original TLS hostname."""

    def __init__(
        self,
        resolver: AddressResolver,
        connection: httpx.AsyncBaseTransport,
    ) -> None:
        self._resolver = resolver
        self._connection = connection
        self._closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        port = request.url.port
        if port is None:
            port = 443 if request.url.scheme == "https" else 80
        address = await _pinned_address(host, port, self._resolver)
        headers = request.headers.copy()
        headers["host"] = _host_header(request.url)
        pinned = httpx.Request(
            method=request.method,
            url=request.url.copy_with(host=address),
            headers=headers,
            extensions={**dict(request.extensions), "sni_hostname": host},
        )
        return await self._connection.handle_async_request(pinned)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._connection.aclose()


async def fetch_public_page(
    url: str,
    *,
    max_bytes: int,
    max_redirects: int,
    timeout_seconds: float,
    resolver: AddressResolver | None = None,
    connection: httpx.AsyncBaseTransport | None = None,
) -> FetchedPage:
    """Fetch one public HTML page. Unsafe targets raise before a connection."""
    current = parse_public_url(url)
    resolve = resolver if resolver is not None else system_resolver
    inner = connection or httpx.AsyncHTTPTransport(trust_env=False)
    pinned = PinnedTransport(resolve, inner)
    try:
        async with httpx.AsyncClient(
            transport=pinned,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(timeout_seconds),
        ) as client:
            try:
                async with asyncio.timeout(timeout_seconds):
                    return await _follow(
                        client,
                        current,
                        max_bytes=max_bytes,
                        max_redirects=max_redirects,
                    )
            except TimeoutError:
                raise fetch_timeout() from None
    finally:
        await pinned.aclose()


async def _follow(
    client: httpx.AsyncClient,
    current: httpx.URL,
    *,
    max_bytes: int,
    max_redirects: int,
) -> FetchedPage:
    redirects = 0
    while True:
        try:
            request = client.build_request("GET", str(current))
            response = await client.send(request, stream=True)
        except httpx.TimeoutException:
            raise fetch_timeout() from None
        except httpx.HTTPError:
            raise fetch_failed() from None
        page_body: bytes | None = None
        try:
            if response.status_code in _REDIRECTS:
                if redirects >= max_redirects:
                    raise fetch_failed()
                location = response.headers.get("location")
                if not location or not location.strip():
                    raise fetch_failed()
                current = _next_url(current, location)
                redirects += 1
                continue
            if response.status_code != 200:
                raise fetch_failed()
            page_body = await _read_body(response, max_bytes)
        finally:
            await response.aclose()
        if page_body is None:
            continue
        if not _is_html(response.headers.get("content-type", "")):
            raise fetch_failed()
        rendered = canonical_url(current)
        if len(rendered) > 2048:
            raise unsafe_url()
        return FetchedPage(url=rendered, body=page_body)


def _next_url(current: httpx.URL, location: str) -> httpx.URL:
    try:
        joined = urljoin(str(current), location.strip())
    except ValueError:
        raise unsafe_url() from None
    return parse_public_url(joined)


async def _pinned_address(
    host: str,
    port: int,
    resolver: AddressResolver,
) -> str:
    literal = _literal_ip(host)
    addresses = [literal] if literal is not None else list(await resolver(host, port))
    if not addresses or any(not _is_public(address) for address in addresses):
        raise unsafe_url()
    return addresses[0]


async def _read_body(response: httpx.Response, limit: int) -> bytes:
    length = response.headers.get("content-length")
    if length is not None:
        try:
            declared = int(length)
        except ValueError:
            raise fetch_failed() from None
        if declared > limit:
            logger.warning("web rejected: content length")
            raise payload_too_large()
    raw = bytearray()
    async for piece in response.aiter_raw():
        raw.extend(piece)
        if len(raw) > limit:
            logger.warning("web rejected: compressed byte limit")
            raise payload_too_large()
    return _decompress(bytes(raw), response.headers.get("content-encoding"), limit)


def _decompress(data: bytes, encoding: str | None, limit: int) -> bytes:
    label = "" if encoding is None else encoding.split(",", 1)[0].strip().lower()
    if label in {"", "identity"}:
        return data
    if label == "gzip":
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif label == "deflate":
        decompressor = zlib.decompressobj()
    else:
        logger.warning("web rejected: content encoding")
        raise fetch_failed()
    output = bytearray()
    offset = 0
    try:
        while offset < len(data):
            room = limit - len(output) + 1
            piece = decompressor.decompress(data[offset : offset + 4096], room)
            output.extend(piece)
            if len(output) > limit or decompressor.unconsumed_tail:
                logger.warning("web rejected: decompressed byte limit")
                raise payload_too_large()
            offset += 4096
            if decompressor.eof:
                break
        flushed = decompressor.flush()
    except zlib.error:
        raise fetch_failed() from None
    output.extend(flushed)
    if len(output) > limit:
        logger.warning("web rejected: decompressed byte limit")
        raise payload_too_large()
    return bytes(output)


def _is_html(header: str) -> bool:
    media = header.split(";", 1)[0].strip().lower()
    return media in _HTML_TYPES


def _host_header(url: httpx.URL) -> str:
    host = url.host
    if ":" in host:
        host = f"[{host}]"
    port = _explicit_port(url)
    if port is None:
        return host
    return f"{host}:{port}"


def _explicit_port(url: httpx.URL) -> int | None:
    port = url.port
    if port is None:
        return None
    if (url.scheme == "http" and port == 80) or (url.scheme == "https" and port == 443):
        return None
    return port


def _literal_ip(host: str) -> str | None:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None
    return host


def _is_public(address: str) -> bool:
    try:
        parsed: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(
            address,
        )
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    return bool(parsed.is_global)


def _ambiguous_numeric_host(host: str) -> bool:
    labels = host.split(".")
    if len(labels) < 2 or any(not label.isdigit() for label in labels):
        return False
    return any(len(label) > 1 and label.startswith("0") for label in labels)
