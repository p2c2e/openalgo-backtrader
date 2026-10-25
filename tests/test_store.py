from datetime import datetime, timezone

import backtrader as bt

from openalgo_bt.stores.oa import OAStore


class FakeAdapter:
    def __init__(self):
        self.client = object()
        self.calls = []

    def history(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "status": "success",
            "data": [
                {
                    "timestamp": 1722839400,
                    "open": "100",
                    "high": "101",
                    "low": "99.5",
                    "close": "100.5",
                    "volume": "1000",
                }
            ],
        }

    def intervals(self):
        self.calls.append({"endpoint": "intervals"})
        return {
            "status": "success",
            "data": {
                "minutes": ["1m", "3m", "5m", "60m"],
                "hours": ["1h"],
                "days": ["D"],
            },
        }


def make_store(monkeypatch, adapter):
    def fake_init(self, api_key=None, host=None):
        self.api_key = api_key or "secret"
        self.host = host or "http://oa.local"
        self.adapter = adapter
        self.client = adapter.client
        self._broker = None

    monkeypatch.setattr(OAStore, "__init__", fake_init)
    return OAStore()


def test_fetch_historical_routes_through_adapter(monkeypatch):
    adapter = FakeAdapter()
    store = make_store(monkeypatch, adapter)

    candles = store.fetch_historical(
        symbol="NSE:RELIANCE",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_date="2026-01-02",
        interval="1m",
    )

    assert len(candles) == 1
    assert candles[0]["close"] == 100.5
    assert adapter.calls == [
        {
            "symbol": "RELIANCE",
            "exchange": "NSE",
            "interval": "1m",
            "start_date": "2026-01-01",
            "end_date": "2026-01-02",
        }
    ]


def test_bt_to_interval_current_static_mapping():
    assert OAStore.bt_to_interval(bt.TimeFrame.Minutes, 1) == "1m"
    assert OAStore.bt_to_interval(bt.TimeFrame.Minutes, 5) == "5m"
    assert OAStore.bt_to_interval(bt.TimeFrame.Minutes, 60) == "60m"
    assert OAStore.bt_to_interval(bt.TimeFrame.Days, 1) == "D"

def test_available_intervals_is_cached_and_normalized(monkeypatch):
    adapter = FakeAdapter()
    store = make_store(monkeypatch, adapter)

    assert store.available_intervals() == frozenset({"1m", "3m", "5m", "60m", "1h", "D"})
    assert store.available_intervals() == frozenset({"1m", "3m", "5m", "60m", "1h", "D"})
    assert adapter.calls == [{"endpoint": "intervals"}]


def test_resolve_interval_uses_native_or_one_minute_fallback(monkeypatch):
    adapter = FakeAdapter()
    store = make_store(monkeypatch, adapter)

    assert store.resolve_interval(bt.TimeFrame.Minutes, 5) == ("5m", 0)
    assert store.resolve_interval(bt.TimeFrame.Minutes, 2) == ("1m", 2)
    assert store.resolve_interval(bt.TimeFrame.Minutes, 60) == ("60m", 0)


def test_resolve_interval_rejects_unsupported_without_one_minute(monkeypatch):
    class NoMinuteAdapter(FakeAdapter):
        def intervals(self):
            return {"status": "success", "data": {"days": ["D"]}}

    store = make_store(monkeypatch, NoMinuteAdapter())

    try:
        store.resolve_interval(bt.TimeFrame.Minutes, 2)
    except ValueError as exc:
        assert "does not advertise" in str(exc)
    else:
        raise AssertionError("expected unsupported interval error")
