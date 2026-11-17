"""Download an image over HTTPS with strict limits.

The URL comes from a queue message, so it is untrusted input. Rules:

* only ``https``, only hosts in an allow-list (also for every redirect);
* TLS certificates are always verified;
* a hard limit on the number of bytes read, checked while streaming;
* HTTP 4xx (except 408 and 429) is permanent, 5xx and network errors are not.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable, Collection
from dataclasses import dataclass
from http.client import HTTPMessage, HTTPResponse
from typing import IO
from urllib.parse import urlsplit

from thumbnailer.errors import FetchRejected, TransientFetchError

_RETRYABLE_STATUS = {408, 429}
_CHUNK = 64 * 1024
_MAX_REDIRECTS = 3


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    allowed_hosts: frozenset[str]
    max_bytes: int = 20 * 1024 * 1024
    timeout_seconds: float = 10.0
    user_agent: str = "pulumi-sqs-lambda-worker/0.1"


def check_url(url: str, allowed_hosts: Collection[str]) -> None:
    """Raise ``FetchRejected`` unless ``url`` is https on an allowed host."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise FetchRejected(f"only https URLs are allowed, got {parts.scheme or 'none'!r}")
    if parts.username or parts.password:
        raise FetchRejected("URLs with credentials are not allowed")
    host = (parts.hostname or "").lower()
    if host not in allowed_hosts:
        raise FetchRejected(f"host {host!r} is not in the allow-list")
    if parts.port not in (None, 443):
        raise FetchRejected(f"port {parts.port} is not allowed")


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Apply ``check_url`` to every redirect target and limit the chain."""

    max_redirections = _MAX_REDIRECTS

    def __init__(self, allowed_hosts: Collection[str]) -> None:
        self._allowed_hosts = allowed_hosts

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        check_url(newurl, self._allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


Opener = Callable[[urllib.request.Request, float], HTTPResponse]


def _default_opener(policy: FetchPolicy) -> Opener:
    # Build only the handlers we need: no proxy from the environment, no FTP or file://.
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPErrorProcessor(),
        urllib.request.HTTPDefaultErrorHandler(),
        _CheckedRedirects(policy.allowed_hosts),
    ):
        opener.add_handler(handler)

    def open_url(request: urllib.request.Request, timeout: float) -> HTTPResponse:
        response: HTTPResponse = opener.open(request, timeout=timeout)
        return response

    return open_url


def read_limited(stream: IO[bytes], max_bytes: int) -> bytes:
    """Read the whole stream, failing as soon as it passes ``max_bytes``."""
    chunks: list[bytes] = []
    total = 0
    while chunk := stream.read(_CHUNK):
        total += len(chunk)
        if total > max_bytes:
            raise FetchRejected(f"response is larger than {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_image(url: str, policy: FetchPolicy, open_url: Opener | None = None) -> bytes:
    """Download ``url`` and return the body."""
    check_url(url, policy.allowed_hosts)
    opener = open_url or _default_opener(policy)
    request = urllib.request.Request(  # noqa: S310 - check_url allows https only
        url, headers={"User-Agent": policy.user_agent, "Accept": "image/*"}
    )
    try:
        with opener(request, policy.timeout_seconds) as response:
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > policy.max_bytes:
                raise FetchRejected(f"Content-Length {declared} is larger than {policy.max_bytes}")
            return read_limited(response, policy.max_bytes)
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500 and exc.code not in _RETRYABLE_STATUS:
            raise FetchRejected(f"HTTP {exc.code} for {url}") from exc
        raise TransientFetchError(f"HTTP {exc.code} for {url}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise TransientFetchError(f"network error for {url}: {exc}") from exc
