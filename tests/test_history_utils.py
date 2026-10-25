from datetime import datetime

from openalgo_bt.utils.history import cache_directory, fetch_ltp_bulk, get_ltp, history


class FakeStore:
    def __init__(self, candles):
        self.candles = candles
        self.calls = []

    def fetch_historical(self, **kwargs):
        self.calls.append(kwargs)
        return list(self.candles)


def test_history_delegates_to_store_without_import_time_client():
    store = FakeStore([{"datetime": datetime(2026, 1, 2), "close": 101.5}])

    candles = list(
        history(
            "NSE:RELIANCE",
            "2026-01-01",
            datetime(2026, 1, 2),
            interval="1m",
            use_cache=True,
            store=store,
        )
    )

    assert candles == [{"datetime": datetime(2026, 1, 2), "close": 101.5}]
    assert store.calls == [
        {
            "symbol": "NSE:RELIANCE",
            "start": "2026-01-01",
            "end_date": "2026-01-02",
            "interval": "1m",
            "exchange": None,
        }
    ]


def test_cache_directory_is_portable_and_does_not_create_directory(tmp_path, monkeypatch):
    configured = tmp_path / "cache"
    monkeypatch.setenv("OPENALGO_BT_CACHE_DIR", str(configured))

    assert cache_directory() == configured
    assert not configured.exists()


def test_get_ltp_normalizes_compact_dates_and_requires_close():
    store = FakeStore([{"datetime": datetime(2026, 1, 2), "close": "99.25"}])

    assert get_ltp("NSE:RELIANCE", "20260102", store=store) == 99.25
    assert store.calls[0]["start"] == "2026-01-02"
    assert store.calls[0]["end_date"] == "2026-01-02"


def test_fetch_ltp_bulk_keeps_per_symbol_failures():
    class SelectiveStore(FakeStore):
        def fetch_historical(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["symbol"] == "NSE:MISSING":
                return []
            return [{"datetime": datetime(2026, 1, 2), "close": 10}]

    store = SelectiveStore([])

    assert fetch_ltp_bulk(["NSE:RELIANCE", "NSE:MISSING"], "2026-01-02", store=store) == {
        "NSE:RELIANCE": 10.0,
        "NSE:MISSING": None,
    }
