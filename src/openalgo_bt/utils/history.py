"""Historical-data helpers built on the shared OpenAlgo adapter boundary.

The functions in this module retain the original public names, but client creation
and caching are now explicit and portable. Importing this module performs no
network I/O and creates no cache directories.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from openalgo_bt.adapters.openalgo import OpenAlgoAdapter, normalize_history_candles
from openalgo_bt.stores.oa import OAStore


DEFAULT_CACHE_DIR = Path.home() / ".cache" / "openalgo-backtrader"


def _date_string(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _symbol_exchange(symbol: str, exchange: Optional[str]) -> tuple[str, str]:
    parsed_exchange = None
    parsed_symbol = symbol
    if ":" in symbol:
        parsed_exchange, parsed_symbol = symbol.split(":", 1)
    return exchange or parsed_exchange or "NSE", parsed_symbol


def cache_directory(value: Optional[str] = None) -> Path:
    """Return the configured cache location without creating it."""
    configured = value or os.getenv("OPENALGO_BT_CACHE_DIR")
    return Path(configured).expanduser() if configured else DEFAULT_CACHE_DIR


def history(
    symbol: str,
    start: date | datetime | str,
    end_date: date | datetime | str,
    interval: str = "D",
    exchange: Optional[str] = None,
    use_cache: bool = False,
    *,
    store: Optional[OAStore] = None,
) -> Iterator[dict[str, Any]]:
    """Yield normalized historical candles for a symbol.

    ``use_cache`` is retained for call compatibility but deliberately does not
    enable persistence until cache invalidation semantics are implemented. A
    caller requesting it receives the same normalized live result.
    """
    del use_cache
    client = store or OAStore()
    candles = client.fetch_historical(
        symbol=symbol,
        start=_date_string(start),
        end_date=_date_string(end_date),
        interval=interval,
        exchange=exchange,
    )
    yield from candles


def get_ltp(
    symbol: str,
    current_date: date | datetime | str,
    *,
    store: Optional[OAStore] = None,
) -> float:
    """Return the daily close price for ``symbol`` on ``current_date``."""
    query_date = _date_string(current_date).replace("/", "-")
    if len(query_date) == 8 and query_date.isdigit():
        query_date = f"{query_date[:4]}-{query_date[4:6]}-{query_date[6:]}"

    candles = list(history(symbol, query_date, query_date, interval="D", store=store))
    if not candles or candles[0].get("close") is None:
        raise ValueError(f"LTP fetch failed: no daily candle for {symbol} on {query_date}")
    return float(candles[0]["close"])


def fetch_ltp_bulk(
    symbols: list[str],
    current_date: date | datetime | str,
    *,
    store: Optional[OAStore] = None,
) -> dict[str, Optional[float]]:
    """Fetch daily closes, retaining failures per symbol rather than aborting."""
    return {
        symbol: _get_ltp_or_none(symbol, current_date, store=store)
        for symbol in symbols
    }


def _get_ltp_or_none(
    symbol: str,
    current_date: date | datetime | str,
    *,
    store: Optional[OAStore],
) -> Optional[float]:
    try:
        return get_ltp(symbol, current_date, store=store)
    except (ValueError, RuntimeError, OSError):
        return None


def fetch_930am_price(
    symbol: str,
    value: date | datetime,
    *,
    exchange: Optional[str] = None,
    adapter: Optional[OpenAlgoAdapter] = None,
) -> Optional[float]:
    """Return the 09:30 local-time close from 15-minute candles, if present."""
    client = adapter or OpenAlgoAdapter()
    final_exchange, final_symbol = _symbol_exchange(symbol, exchange)
    response = client.history(
        symbol=final_symbol,
        exchange=final_exchange,
        interval="15m",
        start_date=_date_string(value),
        end_date=_date_string(value),
    )
    for candle in normalize_history_candles(response):
        timestamp = candle["datetime"]
        if timestamp.strftime("%H:%M") == "09:30":
            return float(candle["close"])
    return None


__all__ = [
    "DEFAULT_CACHE_DIR",
    "cache_directory",
    "fetch_930am_price",
    "fetch_ltp_bulk",
    "get_ltp",
    "history",
]
