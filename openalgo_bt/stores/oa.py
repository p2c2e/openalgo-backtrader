import os
from typing import Iterable, List, Optional, Dict, Any, Tuple
from datetime import datetime
from dotenv import load_dotenv

# Backtrader is optional at runtime for this store helper (we only reference constants)
try:
    import backtrader as bt
except Exception:  # pragma: no cover
    bt = None  # Allows using this module without BT installed for linting/type-checking

# OpenAlgo Python client
from openalgo import api

# Reuse existing project utilities for historical data
from openalgo_bt.utils.history import history as oa_history


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

        # Initialize OpenAlgo client
        self.client = api(api_key=self.api_key, host=self.host)
        # Cached broker instance (lazy)
        self._broker = None

    # -------------------------
    # Timeframe/Compression Map
    # -------------------------
    @staticmethod
    def bt_to_interval(timeframe: Any, compression: int) -> str:
        """
        Convert Backtrader timeframe/compression to OpenAlgo interval string.

        Supported:
          - Minutes: 1, 3, 5, 10, 15, 30  -> '1m', '3m', '5m', '10m', '15m', '30m'
          - Hours:   1                    -> '1h'
          - Days:    any compression (1)  -> 'D'

        Raises ValueError for unsupported combinations.
        """
        # Allow calling without backtrader installed, using numeric constants if available
        tf_minutes = getattr(bt.TimeFrame, "Minutes", 1) if bt else 1
        tf_hours = getattr(bt.TimeFrame, "Hours", 2) if bt else 2
        tf_days = getattr(bt.TimeFrame, "Days", 3) if bt else 3

        if timeframe == tf_minutes:
            valid = {1: "1m", 3: "3m", 5: "5m", 10: "10m", 15: "15m", 30: "30m"}
            if compression in valid:
                return valid[compression]
            raise ValueError(f"Unsupported minutes compression for OpenAlgo: {compression}")

        if timeframe == tf_hours:
            if compression == 1:
                return "1h"
            raise ValueError(f"Unsupported hours compression for OpenAlgo: {compression}")

        if timeframe == tf_days:
            # OpenAlgo daily interval is 'D'
            return "D"

        raise ValueError(f"Unsupported timeframe/compression for OpenAlgo: timeframe={timeframe}, compression={compression}")

    # -------------------------
    # Helpers
    # -------------------------
    @staticmethod
    def _dt_key(candle: Dict[str, Any]) -> datetime:
        dt = candle.get("datetime")
        return dt if isinstance(dt, datetime) else datetime.min

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

        # Use the project's tested utils.history.history generator to ensure consistent tz handling
        candles_iter = oa_history(
            symbol=symbol,
            start=start,
            end_date=end_date,
            interval=interval,
            exchange=exchange,
        )
        candles = list(candles_iter)

        # Sort defensively by datetime
        candles.sort(key=self._dt_key)
        return candles

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
