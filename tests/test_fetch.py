from __future__ import annotations

import io
import urllib.error
import urllib.request
from email.message import Message
from http.client import HTTPMessage
from typing import Any, cast

import pytest

from thumbnailer.errors import FetchRejected, TransientFetchError
from thumbnailer.fetch import FetchPolicy, _CheckedRedirects, check_url, fetch_image

HOST = "upload.wikimedia.org"
URL = f"https://{HOST}/wikipedia/commons/a/a9/Example.jpg"
POLICY = FetchPolicy(allowed_hosts=frozenset({HOST}), max_bytes=1000, user_agent="test-agent/1")


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        super().__init__(body)
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value


class FakeOpener:
    def __init__(self, response: FakeResponse | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> Any:
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.response


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(URL, code, "error", Message(), None)


@pytest.mark.parametrize(
    "url",
    [
        "http://upload.wikimedia.org/a.jpg",
        "ftp://upload.wikimedia.org/a.jpg",
        "file:///etc/passwd",
        "https://user:secret@upload.wikimedia.org/a.jpg",
        "https://upload.wikimedia.org.evil.example/a.jpg",
        "https://169.254.169.254/latest/meta-data/",
        "https://upload.wikimedia.org:8443/a.jpg",
        "not a url",
    ],
)
def test_check_url_rejects(url: str) -> None:
    with pytest.raises(FetchRejected):
        check_url(url, {HOST})


def test_check_url_accepts_allowed_host_in_any_case() -> None:
    check_url("https://Upload.Wikimedia.ORG:443/x.png", {HOST})


def test_returns_body_and_sends_user_agent() -> None:
    opener = FakeOpener(FakeResponse(b"image-bytes"))
    assert fetch_image(URL, POLICY, open_url=opener) == b"image-bytes"
    assert opener.requests[0].get_header("User-agent") == "test-agent/1"


def test_rejected_url_is_never_opened() -> None:
    opener = FakeOpener(FakeResponse(b""))
    with pytest.raises(FetchRejected):
        fetch_image("http://example.com/a.jpg", POLICY, open_url=opener)
    assert opener.requests == []


def test_rejects_large_content_length_before_reading() -> None:
    opener = FakeOpener(FakeResponse(b"x", headers={"Content-Length": "5000"}))
    with pytest.raises(FetchRejected, match="Content-Length"):
        fetch_image(URL, POLICY, open_url=opener)


def test_rejects_body_larger_than_limit_without_content_length() -> None:
    opener = FakeOpener(FakeResponse(b"x" * 1001))
    with pytest.raises(FetchRejected, match="larger than"):
        fetch_image(URL, POLICY, open_url=opener)


@pytest.mark.parametrize(("code", "expected"), [(404, FetchRejected), (403, FetchRejected)])
def test_client_errors_are_permanent(code: int, expected: type[Exception]) -> None:
    with pytest.raises(expected):
        fetch_image(URL, POLICY, open_url=FakeOpener(error=http_error(code)))


@pytest.mark.parametrize("code", [408, 429, 500, 503])
def test_server_errors_and_throttling_are_transient(code: int) -> None:
    with pytest.raises(TransientFetchError):
        fetch_image(URL, POLICY, open_url=FakeOpener(error=http_error(code)))


@pytest.mark.parametrize(
    "error", [urllib.error.URLError("dns failure"), TimeoutError(), ConnectionResetError()]
)
def test_network_errors_are_transient(error: Exception) -> None:
    with pytest.raises(TransientFetchError):
        fetch_image(URL, POLICY, open_url=FakeOpener(error=error))


def test_redirect_to_other_host_is_rejected() -> None:
    handler = _CheckedRedirects({HOST})
    request = urllib.request.Request(URL)
    with pytest.raises(FetchRejected):
        handler.redirect_request(
            request, cast(Any, None), 302, "Found", HTTPMessage(), "https://evil.example/x"
        )


def test_redirect_to_allowed_host_is_followed() -> None:
    handler = _CheckedRedirects({HOST})
    request = urllib.request.Request(URL)
    new = handler.redirect_request(
        request, cast(Any, None), 301, "Moved", HTTPMessage(), f"https://{HOST}/other.jpg"
    )
    assert new is not None
    assert new.full_url == f"https://{HOST}/other.jpg"
