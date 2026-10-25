import os
from typing import List, Optional, Dict, Any, FrozenSet, Tuple
from datetime import datetime

from dotenv import load_dotenv

# Backtrader is optional at runtime for this store helper (we only reference constants)
try:
    import backtrader as bt
except Exception:  # pragma: no cover
    bt = None  # Allows using this module without BT installed for linting/type-checking

from openalgo_bt.adapters.openalgo import OpenAlgoAdapter, normalize_history_candles


class OAStore:
    """
    OAStore - OpenAlgo Store helper for Backtrader integrations.

    Responsibilities:
    - Manage OpenAlgo API client configuration from environment / parameters.
    - Map Backtrader timeframe/compression to OpenAlgo intervals.
    - Provide convenience wrappers to fetch historical candles compatible with BT feeds.

    Environment Variables:
      - OPENALGO_API_KEY  : API key (required)
      - OPENALGO_API_HOST : API host (default: http://127.0.0.1:5000)

    Example:
        store = OAStore()
        interval = store.bt_to_interval(bt.TimeFrame.Minutes, 5)
        candles = store.fetch_historical(
            symbol="NSE:TCS",
            start="2025-01-01",
            end_date="2025-01-31",
            interval=interval
        )
    """

    DEFAULT_HOST = "http://127.0.0.1:5000"

    def __init__(
        self,
        api_key: Optional[str] = None,
        host: Optional[str] = None,
    ) -> None:
        load_dotenv()
        self.api_key = api_key or os.getenv("OPENALGO_API_KEY")
        self.host = host or os.getenv("OPENALGO_API_HOST", self.DEFAULT_HOST)

        if not self.api_key:
            raise ValueError("OPENALGO_API_KEY is not set. Configure it in your environment or .env file.")

        # Initialize OpenAlgo adapter and expose the raw SDK client for broker compatibility.
        self.adapter = OpenAlgoAdapter(api_key=self.api_key, host=self.host)
        self.client = self.adapter.client
        # Cached broker instance (lazy)
        self._broker = None
        self._available_intervals: Optional[FrozenSet[str]] = None

    # -------------------------
    # Timeframe/Compression Map
    # -------------------------
    @staticmethod
    def bt_to_interval(timeframe: Any, compression: int) -> str:
        """
        Convert Backtrader timeframe/compression to OpenAlgo interval string.

        Supported:
          - Minutes: 1, 3, 5, 10, 15, 30, 60 -> OpenAlgo minute interval
          - Backtrader represents hourly bars as Minutes with compression 60.
          - Days:    any compression (1)  -> 'D'

        Raises ValueError for unsupported combinations.
        """
        # Allow calling without backtrader installed, using numeric constants if available
        tf_minutes = getattr(bt.TimeFrame, "Minutes", 1) if bt else 1
        tf_hours = getattr(bt.TimeFrame, "Hours", 2) if bt else 2
        tf_days = getattr(bt.TimeFrame, "Days", 3) if bt else 3

        if timeframe == tf_minutes:
            valid = {1: "1m", 3: "3m", 5: "5m", 10: "10m", 15: "15m", 30: "30m", 60: "60m"}
            if compression in valid:
                return valid[compression]
            raise ValueError(f"Unsupported minutes compression for OpenAlgo: {compression}")


        if timeframe == tf_days:
            # OpenAlgo daily interval is 'D'
            return "D"

        raise ValueError(f"Unsupported timeframe/compression for OpenAlgo: timeframe={timeframe}, compression={compression}")

    def available_intervals(self, *, refresh: bool = False) -> FrozenSet[str]:
        """Return normalized intervals advertised by the OpenAlgo target.

        The result is cached per store instance. Callers must explicitly request
        refresh when a server-side capability change should be observed.
        """
        cached = getattr(self, "_available_intervals", None)
        if cached is not None and not refresh:
            return cached

        response = self.adapter.intervals()
        data = response.data if hasattr(response, "data") else response.get("data", response)
        if not isinstance(data, dict):
            raise ValueError("OpenAlgo intervals response must contain an object")

        intervals = {
            str(interval).strip()
            for values in data.values()
            if isinstance(values, (list, tuple, set))
            for interval in values
            if isinstance(interval, str) and interval.strip()
        }
        if not intervals:
            raise ValueError("OpenAlgo intervals response did not contain supported intervals")

        self._available_intervals = frozenset(intervals)
        return self._available_intervals

    def supports_interval(self, interval: str, *, refresh: bool = False) -> bool:
        """Return whether the target explicitly advertises ``interval``."""
        return interval in self.available_intervals(refresh=refresh)

    def resolve_interval(
        self,
        timeframe: Any,
        compression: int,
        *,
        refresh: bool = False,
    ) -> Tuple[str, int]:
        """Choose a native interval or a documented 1-minute aggregation fallback.

        Returns ``(interval, local_resample_minutes)``. A zero resample value
        means the requested interval is natively advertised by the server.
        """
        tf_minutes = getattr(bt.TimeFrame, "Minutes", 1) if bt else 1
        tf_hours = getattr(bt.TimeFrame, "Hours", 2) if bt else 2
        tf_days = getattr(bt.TimeFrame, "Days", 3) if bt else 3
        if timeframe == tf_minutes:
            requested = f"{int(compression)}m"
        elif timeframe == tf_days:
            requested = "D"
        else:
            raise ValueError(
                f"Unsupported timeframe/compression for OpenAlgo: timeframe={timeframe}, compression={compression}"
            )

        if self.supports_interval(requested, refresh=refresh):
            return requested, 0

        if timeframe == tf_minutes and compression > 1 and self.supports_interval("1m"):
            return "1m", int(compression)

        raise ValueError(
            f"OpenAlgo target does not advertise {requested!r} and no supported 1m fallback exists"
        )

    # -------------------------
    # Helpers
    # -------------------------
    @staticmethod
    def _dt_key(candle: Dict[str, Any]) -> float:
        dt = candle.get("datetime")
        return dt.timestamp() if isinstance(dt, datetime) else 0.0

    # -------------------------
    # Historical Data
    # -------------------------
    def fetch_historical(
        self,
        symbol: str,
        start: datetime | str,
        end_date: datetime | str,
        interval: Optional[str] = None,
        *,
        timeframe: Optional[Any] = None,
        compression: Optional[int] = None,
        exchange: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical candles from OpenAlgo as a list of dicts:
          [{'datetime': pd.Timestamp, 'open': ..., 'high': ..., 'low': ..., 'close': ..., 'volume': ...}, ...]

        You can either:
          - provide interval directly, or
          - provide timeframe+compression (Backtrader) to be mapped to interval.

        Args:
          symbol: e.g. 'NSE:TCS' or 'NSE_INDEX:NIFTY'
          start, end_date: 'YYYY-MM-DD' or datetime
          interval: OpenAlgo interval (e.g. 'D', '5m', '15m', '1h')
          timeframe, compression: Backtrader timeframe/compression (if interval not given)
          exchange: overrides exchange inferred from 'symbol' (optional)

        Returns:
          List of dict candles sorted by datetime ascending.
        """
        if not interval:
            if timeframe is None or compression is None:
                raise ValueError("Either interval or both timeframe and compression must be provided")
            interval = self.bt_to_interval(timeframe, compression)

        parsed_exchange, parsed_symbol = self._split_symbol_exchange(symbol, exchange)
        response = self.adapter.history(
            symbol=parsed_symbol,
            exchange=parsed_exchange,
            interval=interval,
            start_date=self._date_str(start),
            end_date=self._date_str(end_date),
        )
        candles = normalize_history_candles(response)

        # Sort defensively by datetime
        candles.sort(key=self._dt_key)
        return candles

    @staticmethod
    def _date_str(value: datetime | str) -> str:
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d")
        return str(value)

    @staticmethod
    def _split_symbol_exchange(symbol: str, exchange: Optional[str]) -> tuple[str, str]:
        parsed_exchange = None
        parsed_symbol = symbol
        if isinstance(symbol, str) and ":" in symbol:
            parts = symbol.split(":", 1)
            if len(parts) == 2:
                parsed_exchange, parsed_symbol = parts[0], parts[1]
        final_exchange = exchange if exchange else (parsed_exchange if parsed_exchange else "NSE")
        return final_exchange, parsed_symbol

    # -------------------------
    # Direct Client Access
    # -------------------------
    def get_client(self):
        """Expose raw OpenAlgo client if needed for advanced operations."""
        return self.client

    # -------------------------
    # Broker Accessor
    # -------------------------
    def getbroker(self, **kwargs):
        """
        Return a Backtrader-compatible broker bound to this store.
        Mirrors the IBStore.getbroker pattern: cache and reuse a single instance.

        kwargs are forwarded to the broker constructor (e.g., cash_fallback, product, strategy, debug).
        """
        if self._broker is None:
            # Import here to avoid hard dependency during simple data-only use
            from openalgo_bt.brokers.oabroker import OABroker  # type: ignore
            broker = OABroker(store=self)
            # Apply any passed-in params if they match broker params
            for k, v in kwargs.items():
                if hasattr(broker.params, k):  # type: ignore[attr-defined]
                    setattr(broker.params, k, v)  # type: ignore[attr-defined]
            self._broker = broker
        return self._broker


__all__ = ["OAStore"]
