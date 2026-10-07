import logging

import httpx
import pytest

from src.errors import APIError


def test_retries_three_times_with_backoff(http_factory, caplog):
    attempts = []
    sleeps = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(503, text="sensitive upstream error")

    http = http_factory(handler, max_retries=3, sleeps=sleeps)
    with caplog.at_level(logging.WARNING), pytest.raises(APIError) as exc:
        http.json("GET", "https://example.org/secret-token", service="Example")
    assert len(attempts) == 4
    assert sleeps == [1, 2, 4]
    assert "secret-token" not in str(exc.value) + caplog.text
    assert "sensitive" not in str(exc.value) + caplog.text


def test_retry_after_is_respected(http_factory):
    sleeps = []
    responses = iter(
        [httpx.Response(429, headers={"Retry-After": "5"}), httpx.Response(200, json={"ok": True})]
    )
    http = http_factory(lambda request: next(responses), max_retries=3, sleeps=sleeps)
    assert http.json("GET", "https://example.org", service="Example") == {"ok": True}
    assert sleeps == [5]


def test_transport_error_can_recover(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("private details", request=request)
        return httpx.Response(200, json={})

    http = http_factory(handler, max_retries=3)
    assert http.json("GET", "https://example.org", service="Example") == {}
    assert len(calls) == 2


def test_permanent_error_is_not_retried(http_factory):
    calls = []
    http = http_factory(lambda request: calls.append(request) or httpx.Response(403), max_retries=3)
    with pytest.raises(APIError):
        http.json("GET", "https://example.org", service="Example")
    assert len(calls) == 1


def test_html_success_response_is_an_error(http_factory):
    http = http_factory(lambda request: httpx.Response(200, text="<html>bot challenge</html>"))
    with pytest.raises(APIError, match="DBLP"):
        http.json("GET", "https://dblp.org/search/publ/api", service="DBLP")
