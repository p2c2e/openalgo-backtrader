from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from openalgo_bt.feeds.oa import OAData


IST = ZoneInfo("Asia/Kolkata")


def candle(at, open_, high, low, close, volume):
    return {
        "datetime": at,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "openinterest": 0,
    }


def resample(candles, minutes):
    feed = object.__new__(OAData)
    return feed._resample_candles_minutes(candles, minutes)


def test_resample_five_minute_bucket_uses_ist_alignment_and_ohlcv_rules():
    start = datetime(2026, 8, 31, 9, 15, tzinfo=IST)
    candles = [
        candle(start + timedelta(minutes=offset), 100 + offset, 102 + offset, 99 - offset, 101 + offset, 10)
        for offset in range(5)
    ]

    result = resample(candles, 5)

    assert len(result) == 1
    bar = result[0]
    assert bar["datetime"] == start
    assert bar["open"] == 100
    assert bar["high"] == 106
    assert bar["low"] == 95
    assert bar["close"] == 105
    assert bar["volume"] == 50


def test_resample_hour_bucket_converts_utc_input_to_ist_before_flooring():
    # 03:46 UTC is 09:16 IST. Both rows belong to the 09:00 IST hour bucket.
    start_utc = datetime(2026, 8, 31, 3, 46, tzinfo=timezone.utc)
    candles = [
        candle(start_utc, 100, 101, 99, 100.5, 10),
        candle(start_utc + timedelta(minutes=13), 100.5, 103, 100, 102, 15),
    ]

    result = resample(candles, 60)

    assert len(result) == 1
    bar = result[0]
    assert bar["datetime"] == datetime(2026, 8, 31, 9, 0, tzinfo=IST)
    assert bar["open"] == 100
    assert bar["high"] == 103
    assert bar["low"] == 99
    assert bar["close"] == 102
    assert bar["volume"] == 25


def test_resample_creates_new_bucket_at_ist_boundary():
    candles = [
        candle(datetime(2026, 8, 31, 9, 19, tzinfo=IST), 100, 101, 99, 100, 5),
        candle(datetime(2026, 8, 31, 9, 20, tzinfo=IST), 101, 102, 100, 101, 7),
    ]

    result = resample(candles, 5)

    assert [bar["datetime"].strftime("%H:%M") for bar in result] == ["09:15", "09:20"]
    assert [bar["volume"] for bar in result] == [5, 7]
