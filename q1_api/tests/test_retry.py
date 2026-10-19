"""The shared retry helper: what is retried, and how each failure is described."""
import httpx
import pytest

from app.retry import PermanentError, RetriesExhausted, post_with_retries


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=httpx.Timeout(5, connect=2))


def test_client_errors_are_not_retried():
    calls = []

    def bad_request(request):
        calls.append(request)
        return httpx.Response(400, json={"error": "need JSON body {case_id, image_ref}"})

    with pytest.raises(PermanentError, match="HTTP 400: need JSON body"):
        post_with_retries(client_for(bad_request), "http://ocr/parse", max_attempts=6, label="test", json={})
    assert len(calls) == 1


@pytest.mark.parametrize(
    "error, description",
    [
        pytest.param(httpx.ConnectTimeout, "could not connect within 2 s", id="connect-timeout"),
        pytest.param(httpx.ReadTimeout, "no response within 5 s", id="read-timeout"),
        pytest.param(httpx.ConnectError, "request failed: ConnectError('refused')", id="connection-refused"),
    ],
)
def test_transient_errors_are_retried_and_described(error, description):
    calls = []

    def failing(request):
        calls.append(request)
        raise error("refused", request=request)

    with pytest.raises(RetriesExhausted) as raised:
        post_with_retries(client_for(failing), "http://ocr/parse", max_attempts=3, label="test", json={})
    assert str(raised.value) == f"no result after 3 attempts; last error: {description}"
    assert len(calls) == 3
