import httpx
import pytest
from google.genai.errors import APIError

from services import gemini_retry
from services.gemini_retry import generate_content_with_retry


def api_error(status_code: int) -> APIError:
    response = httpx.Response(
        status_code,
        json={"error": {"message": "temporary failure", "status": "UNAVAILABLE"}},
    )
    return APIError(status_code, response)


def test_retries_temporary_gemini_failures_with_bounded_backoff(monkeypatch):
    calls = 0
    delays = []

    def generate_content(**kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise api_error(503)
        return "response"

    monkeypatch.setattr(gemini_retry.time, "sleep", delays.append)

    result = generate_content_with_retry(
        generate_content,
        operation="test",
        model="test-model",
    )

    assert result == "response"
    assert calls == 3
    assert delays == [1, 2]


def test_does_not_retry_non_transient_gemini_errors(monkeypatch):
    calls = 0
    monkeypatch.setattr(gemini_retry.time, "sleep", lambda _: pytest.fail("unexpected retry"))

    def generate_content(**kwargs):
        nonlocal calls
        calls += 1
        raise api_error(400)

    with pytest.raises(APIError):
        generate_content_with_retry(generate_content, operation="test")

    assert calls == 1


def test_raises_after_retry_limit_for_temporary_failures(monkeypatch):
    calls = 0
    delays = []
    monkeypatch.setattr(gemini_retry.time, "sleep", delays.append)

    def generate_content(**kwargs):
        nonlocal calls
        calls += 1
        raise api_error(503)

    with pytest.raises(APIError):
        generate_content_with_retry(generate_content, operation="test")

    assert calls == 3
    assert delays == [1, 2]
