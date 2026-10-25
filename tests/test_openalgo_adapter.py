import json
from pathlib import Path

import pytest

from openalgo_bt.adapters.openalgo import (
    OpenAlgoAdapter,
    OpenAlgoAuthError,
    OpenAlgoUnsupportedEndpointError,
    normalize_history_candles,
    normalize_openalgo_response,
)


FIXTURES = Path(__file__).parent / "fixtures" / "openalgo"


def load_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_json(name: str):
    return json.loads(load_text(name))


def test_normalizes_data_envelope():
    response = normalize_openalgo_response(load_json("history_success.json"))

    assert response.ok
    assert response.status == "success"
    assert response.source_key == "data"
    assert isinstance(response.data, list)
    assert response.data[0]["close"] == 100.5


def test_normalizes_results_envelope():
    response = normalize_openalgo_response(load_json("multiquotes_results.json"))

    assert response.ok
    assert response.status == "success"
    assert response.source_key == "results"
    assert response.data[0]["symbol"] == "RELIANCE"


def test_normalizes_json_text():
    response = normalize_openalgo_response(load_text("intervals_success.json"))

    assert response.ok
    assert "minutes" in response.data
    assert "2m" in response.data["minutes"]


def test_detects_html_endpoint_response():
    with pytest.raises(OpenAlgoUnsupportedEndpointError):
        normalize_openalgo_response(load_text("missing_endpoint_html.html"))


def test_detects_invalid_api_key_message():
    with pytest.raises(OpenAlgoAuthError):
        normalize_openalgo_response({"status": "error", "message": "Invalid openalgo apikey"})


def test_direct_rest_post_injects_apikey_and_normalizes_response():
    calls = []

    def fake_post(url, body, timeout):
        calls.append((url, body, timeout))
        return {"status": "success", "data": {"message": "pong"}}

    adapter = OpenAlgoAdapter(api_key="secret", host="http://oa.local", client=object(), http_post=fake_post)
    response = adapter.ping()

    assert response.ok
    assert response.data == {"message": "pong"}
    assert calls == [("http://oa.local/api/v1/ping", {"apikey": "secret"}, 10.0)]


def test_history_rest_helper_sends_expected_payload():
    calls = []

    def fake_post(url, body, timeout):
        calls.append((url, body, timeout))
        return load_json("history_success.json")

    adapter = OpenAlgoAdapter(api_key="secret", host="http://oa.local/", client=object(), http_post=fake_post)
    response = adapter.history(
        symbol="RELIANCE",
        exchange="NSE",
        interval="1m",
        start_date="2026-01-01",
        end_date="2026-01-02",
    )

    assert response.data[0]["close"] == 100.5
    assert calls[0][0] == "http://oa.local/api/v1/history"
    assert calls[0][1] == {
        "apikey": "secret",
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "interval": "1m",
        "start_date": "2026-01-01",
        "end_date": "2026-01-02",
    }

def test_orderstatus_rest_helper_sends_expected_payload():
    calls = []

    def fake_post(url, body, timeout):
        calls.append((url, body, timeout))
        return {"status": "success", "data": {"order_status": "open"}}

    adapter = OpenAlgoAdapter(api_key="secret", host="http://oa.local", client=object(), http_post=fake_post)
    response = adapter.orderstatus(order_id="order-1", strategy="BackTrader Polling")

    assert response.data == {"order_status": "open"}
    assert calls == [
        (
            "http://oa.local/api/v1/orderstatus",
            {"apikey": "secret", "order_id": "order-1", "strategy": "BackTrader Polling"},
            10.0,
        )
    ]


def test_normalizes_history_candles_from_envelope():
    response = normalize_openalgo_response(load_json("history_success.json"))
    candles = normalize_history_candles(response)

    assert len(candles) == 1
    assert candles[0]["datetime"].year == 2024
    assert candles[0]["open"] == 100.0
    assert candles[0]["close"] == 100.5
    assert candles[0]["openinterest"] == 0.0


def test_normalizes_history_candles_from_list_row():
    candles = normalize_history_candles([[1722839400000, "100", "101", "99.5", "100.5", "1000", "0"]])

    assert len(candles) == 1
    assert candles[0]["datetime"].year == 2024
    assert candles[0]["high"] == 101.0
    assert candles[0]["volume"] == 1000.0
