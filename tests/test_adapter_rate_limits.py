"""Offline HTTP and SDK throttle evidence without exposing backend messages."""

import io
import json
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.error import HTTPError

import pytest

from openalgo_bt.adapters import openalgo as module


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(module, "load_dotenv", None)
    return module.OpenAlgoAdapter(api_key="offline", client=object())


@pytest.mark.parametrize("payload", [
    {"status": "error", "message": "Rate limit exceeded. secret", "retry_after": 7},
    {"status": "error", "message": "Too many requests", "retry_after": "7"},
    {"status": "error", "code": 429, "retry_after": 7},
    {"status": "error", "status_code": "429", "retry_after": 7},
])
def test_failed_sdk_rate_envelope_has_typed_redacted_error(payload):
    with pytest.raises(module.OpenAlgoRateLimitError) as caught:
        module.normalize_openalgo_response(payload)
    assert caught.value.retry_after == 7
    assert "secret" not in str(caught.value)
    assert str(caught.value) == "OpenAlgo request rate limited"


@pytest.mark.parametrize("hint", [None, True, False, -1, 0, "nan", "inf", "bad", [], {}])
def test_invalid_retry_hint_uses_finite_safe_default(hint):
    with pytest.raises(module.OpenAlgoRateLimitError) as caught:
        module.normalize_openalgo_response({"status": "error", "code": 429, "retry_after": hint})
    assert caught.value.retry_after == 60


@pytest.mark.parametrize("payload", [
    {"status": "success", "data": {"message": "Rate limit exceeded"}},
    {"status": "success", "message": "Too many requests"},
    {"status": "error", "message": "Insufficient margin"},
    {"status": "error", "message": "Retry later", "retry_after": 7},
])
def test_unrelated_or_successful_envelopes_are_not_throttles(payload):
    result = module.normalize_openalgo_response(payload)
    assert result.raw == payload


def install_http_error(monkeypatch, body, headers, code=429):
    attempts = []
    def fail(request, timeout):
        attempts.append((request.full_url, timeout))
        raise HTTPError(request.full_url, code, "Offline HTTP failure", headers, io.BytesIO(body))
    monkeypatch.setattr(module, "urlopen", fail)
    return attempts


@pytest.mark.parametrize("body", [b"<html>Rate limited secret</html>", b"not JSON secret", b"", b"[]"])
def test_http_status_is_authoritative_even_with_non_json_body(adapter, monkeypatch, body):
    attempts = install_http_error(monkeypatch, body, {"Retry-After": "3"})
    with pytest.raises(module.OpenAlgoRateLimitError) as caught:
        adapter.funds()
    assert caught.value.retry_after == 3
    assert str(caught.value) == "OpenAlgo request rate limited"
    assert len(attempts) == 1


def test_header_seconds_take_precedence_over_body_hint(adapter, monkeypatch):
    install_http_error(monkeypatch, json.dumps({"retry_after": 20}).encode(), {"Retry-After": "4"})
    with pytest.raises(module.OpenAlgoRateLimitError) as caught:
        adapter.funds()
    assert caught.value.retry_after == 4


def test_http_date_header_uses_wall_clock_not_monotonic(adapter, monkeypatch):
    monkeypatch.setattr(module.time, "time", lambda: 1000.0)
    date = format_datetime(datetime.fromtimestamp(1008, timezone.utc), usegmt=True)
    install_http_error(monkeypatch, b"", {"Retry-After": date})
    with pytest.raises(module.OpenAlgoRateLimitError) as caught:
        adapter.funds()
    assert caught.value.retry_after == 8


@pytest.mark.parametrize("header", [None, "invalid", "-3", "0", "nan", "inf"])
def test_invalid_header_uses_valid_body_hint(adapter, monkeypatch, header):
    install_http_error(monkeypatch, b'{"retry_after": 9}', {"Retry-After": header})
    with pytest.raises(module.OpenAlgoRateLimitError) as caught:
        adapter.funds()
    assert caught.value.retry_after == 9


def test_non_429_error_behavior_is_preserved(adapter, monkeypatch):
    install_http_error(monkeypatch, b'{"status":"error","message":"Invalid api key"}', {}, code=401)
    with pytest.raises(module.OpenAlgoAuthError):
        adapter.funds()


def test_injected_http_and_sdk_use_same_classification(adapter):
    adapter._http_post = lambda *args: {"status": "error", "code": 429, "retry_after": 2}
    with pytest.raises(module.OpenAlgoRateLimitError):
        adapter.funds()
    adapter.client = type("Client", (), {"funds": lambda self: {"status": "error", "code": 429}})()
    with pytest.raises(module.OpenAlgoRateLimitError):
        adapter.call_client("funds")


def test_error_is_exported_at_public_adapter_boundary():
    from openalgo_bt.adapters import OpenAlgoRateLimitError
    assert issubclass(OpenAlgoRateLimitError, module.OpenAlgoAPIError)
