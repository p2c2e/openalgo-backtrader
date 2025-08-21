import os
from collections import deque
from typing import Deque, Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import threading
import json
import time

# Backtrader import with graceful fallback shim for static analysis and optional runtime
try:
    import backtrader as bt  # type: ignore
except Exception:  # pragma: no cover
    class _BTShim:  # minimal shim to satisfy static analyzers when backtrader isn't installed
        class feed:
            class DataBase(object):
                def __init__(self, *args, **kwargs):
                    pass

                def start(self):
                    pass

                def stop(self):
                    pass

        class TimeFrame:
            Days = 0
            Minutes = 1
            Hours = 2

        @staticmethod
        def date2num(dt: datetime) -> float:
            try:
                # Try matplotlib if available for consistent behavior
                from matplotlib.dates import date2num as mdate2num  # type: ignore
                return float(mdate2num(dt))
            except Exception:
                return float(dt.timestamp()) if isinstance(dt, datetime) else 0.0

    bt = _BTShim()  # type: ignore

# Import OAStore (package path: bt/stores/oa.py)
from openalgo_bt.stores.oa import OAStore  # type: ignore

# Default timeframe constant tolerant to missing backtrader stubs
TIMEFRAME_DAYS = getattr(getattr(bt, "TimeFrame", object), "Days", 0)


# -------------
# Shared WebSocket Hub (keyed by ws_url) to multiplex subscriptions
# -------------
class _WSConnection:
    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self.ws = None
        self.thread: Optional[threading.Thread] = None
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.connected = False
        self.api_key = os.getenv("OPENALGO_API_KEY")
        # Map of (exchange, symbol) -> set of OAData consumers
        self.subscribers: Dict[tuple[str, str], set] = {}
        # Reverse map of OAData -> (exchange, symbol, mode)
        self.consumer_topics: Dict[Any, tuple[str, str, int]] = {}

    def ensure_started(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        t = threading.Thread(target=self._run, daemon=True)
        self.thread = t
        t.start()

    def _run(self) -> None:
        try:
            import websocket  # type: ignore
        except Exception:
            # websocket-client not installed
            return

        conn = self

        def on_open(ws):
            with conn.lock:
                conn.connected = True
                # Authenticate once per connection
                try:
                    if conn.api_key:
                        ws.send(json.dumps({"action": "authenticate", "api_key": conn.api_key}))
                except Exception:
                    pass
                # Subscribe to all current topics
                topics = set(conn.consumer_topics.values())  # (exchange, symbol, mode)
            for exch, sym, mode in topics:
                try:
                    ws.send(json.dumps({
                        "action": "subscribe",
                        "symbol": sym,
                        "exchange": exch,
                        "mode": int(mode),
                    }))
                except Exception:
                    pass

        def on_message(ws, message):
            if conn.stop.is_set():
                try:
                    ws.close()
                except Exception:
                    pass
                return

            try:
                msg = json.loads(message)
            except Exception:
                return

            # Ping/Pong
            if isinstance(msg, dict) and msg.get("type") == "ping":
                try:
                    ws.send(json.dumps({"type": "pong"}))
                except Exception:
                    pass
                return

            exch, sym = conn._extract_symbol_exchange(msg)
            if not sym:
                return

            price = conn._parse_price(msg)
            if price is None:
                return

            ts = conn._extract_timestamp(msg)
            dt_utc = conn._to_dt_utc(ts)

            key = (exch or "NSE", sym)
            with conn.lock:
                consumers = list(conn.subscribers.get(key, set()))
            for c in consumers:
                try:
                    # Each OAData aggregates ticks into its own 1m bar
                    c._handle_tick(price, dt_utc)
                except Exception:
                    pass

        def on_error(ws, error):
            # Backoff a bit on errors
            time.sleep(1.0)

        def on_close(ws, close_status_code, close_msg):
            with conn.lock:
                conn.connected = False

        while not conn.stop.is_set():
            try:
                conn.ws = websocket.WebSocketApp(
                    conn.ws_url,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                conn.ws.run_forever()
            except Exception:
                pass
            finally:
                with conn.lock:
                    conn.ws = None
                    conn.connected = False
            # Reconnect delay
            if not conn.stop.is_set():
                time.sleep(1.0)

    def subscribe(self, exchange: str, symbol: str, mode: int, consumer: Any) -> None:
        self.ensure_started()
        key = (exchange or "NSE", symbol)
        should_send = False
        ws = None
        with self.lock:
            s = self.subscribers.get(key)
            if s is None:
                s = set()
                self.subscribers[key] = s
            if consumer not in s:
                s.add(consumer)
            # Track the topic for this consumer
            self.consumer_topics[consumer] = (key[0], key[1], int(mode))
            # Notify LIVE for this consumer immediately (mirrors original behavior)
            try:
                if not getattr(consumer, "_live_started", False) and hasattr(consumer, "put_notification"):
                    consumer.put_notification(consumer.LIVE)  # type: ignore[attr-defined]
                    setattr(consumer, "_live_started", True)
            except Exception:
                pass
            ws = self.ws
            # If first subscriber for this key and connection is active, send subscribe immediately
            should_send = self.connected and len(s) == 1
        if should_send and ws:
            try:
                ws.send(json.dumps({
                    "action": "subscribe",
                    "symbol": key[1],
                    "exchange": key[0],
                    "mode": int(mode),
                }))
            except Exception:
                pass

    def unsubscribe(self, consumer: Any) -> None:
        to_unsub: List[tuple[str, str, int]] = []
        ws = None
        connected = False
        with self.lock:
            topic = self.consumer_topics.pop(consumer, None)
            if topic:
                key = (topic[0], topic[1])
                s = self.subscribers.get(key)
                if s and consumer in s:
                    s.discard(consumer)
                    if len(s) == 0:
                        # No more consumers for this symbol on this connection
                        self.subscribers.pop(key, None)
                        to_unsub.append((key[0], key[1], topic[2]))
            ws = self.ws
            connected = self.connected

        for exch, sym, mode in to_unsub:
            if connected and ws:
                try:
                    ws.send(json.dumps({
                        "action": "unsubscribe",
                        "symbol": sym,
                        "exchange": exch,
                        "mode": int(mode),
                    }))
                except Exception:
                    pass

    def is_empty(self) -> bool:
        with self.lock:
            return len(self.consumer_topics) == 0

    def close(self) -> None:
        with self.lock:
            self.stop.set()
            if self.ws:
                try:
                    self.ws.close()
                except Exception:
                    pass
        if self.thread:
            try:
                self.thread.join(timeout=3.0)
            except Exception:
                pass

    @staticmethod
    def _extract_symbol_exchange(msg: Any) -> tuple[Optional[str], Optional[str]]:
        sym = None
        exch = None

        # Direct fields
        if isinstance(msg, dict):
            for k in ("symbol", "s"):
                v = msg.get(k)
                if isinstance(v, str):
                    sym = v
                    break
            if exch is None:
                for k in ("exchange", "e"):
                    v = msg.get(k)
                    if isinstance(v, str):
                        exch = v
                        break

            # Instrument dict shape
            inst = msg.get("instrument")
            if isinstance(inst, dict):
                if isinstance(inst.get("symbol"), str) and not sym:
                    sym = inst.get("symbol")
                if isinstance(inst.get("exchange"), str) and not exch:
                    exch = inst.get("exchange")

            # Nested 'data'
            d = msg.get("data")
            if isinstance(d, dict):
                if sym is None:
                    for k in ("symbol", "s"):
                        v = d.get(k)
                        if isinstance(v, str):
                            sym = v
                            break
                if exch is None:
                    for k in ("exchange", "e"):
                        v = d.get(k)
                        if isinstance(v, str):
                            exch = v
                            break

        # If combined in "NSE:RELIANCE"
        if isinstance(sym, str) and ":" in sym and not exch:
            parts = sym.split(":", 1)
            if len(parts) == 2:
                exch = parts[0]
                sym = parts[1]

        return (exch or "NSE"), sym

    @staticmethod
    def _parse_price(msg: Any) -> Optional[float]:
        if not isinstance(msg, dict):
            return None
        # Try direct fields
        for k in ("ltp", "last_price", "price", "close", "p"):
            v = msg.get(k)
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                try:
                    return float(v)
                except Exception:
                    pass
        # Try nested 'data'
        d = msg.get("data")
        if isinstance(d, dict):
            for k in ("ltp", "last_price", "price", "close", "p"):
                v = d.get(k)
                if isinstance(v, (int, float)):
                    return float(v)
                if isinstance(v, str):
                    try:
                        return float(v)
                    except Exception:
                        pass
        return None

    @staticmethod
    def _extract_timestamp(msg: Any) -> Any:
        ts = None
        for k in ("timestamp", "ts", "time", "t"):
            if isinstance(msg.get(k), (int, float, str)):
                ts = msg.get(k)
                break
        if ts is None and isinstance(msg.get("data"), dict):
            d = msg["data"]
            for k in ("timestamp", "ts", "time", "t"):
                if isinstance(d.get(k), (int, float, str)):
                    ts = d.get(k)
                    break
        return ts

    @staticmethod
    def _to_dt_utc(ts: Any) -> datetime:
        try:
            if isinstance(ts, (int, float)):
                val = float(ts)
                if val > 1e12:  # ms
                    return datetime.fromtimestamp(val / 1000.0, tz=timezone.utc)
                else:
                    return datetime.fromtimestamp(val, tz=timezone.utc)
            elif isinstance(ts, str):
                try:
                    parsed = datetime.fromisoformat(ts)
                    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                except Exception:
                    return datetime.now(tz=timezone.utc)
            else:
                return datetime.now(tz=timezone.utc)
        except Exception:
            return datetime.now(tz=timezone.utc)


class _WSHub:
    def __init__(self):
        self._conns: Dict[str, _WSConnection] = {}
        self._lock = threading.Lock()

    def subscribe(self, ws_url: str, exchange: str, symbol: str, mode: int, consumer: Any) -> None:
        with self._lock:
            conn = self._conns.get(ws_url)
            if conn is None:
                conn = _WSConnection(ws_url)
                self._conns[ws_url] = conn
        conn.subscribe(exchange, symbol, mode, consumer)

    def unsubscribe(self, ws_url: str, consumer: Any) -> None:
        with self._lock:
            conn = self._conns.get(ws_url)
        if not conn:
            return
        conn.unsubscribe(consumer)
        if conn.is_empty():
            conn.close()
            with self._lock:
                self._conns.pop(ws_url, None)


# Global hub instance
WS_HUB = _WSHub()


class OAData(bt.feed.DataBase):  # type: ignore[misc]
    """
    OpenAlgo Backtrader Data Feed (historical-only for now).

    Parameters (Backtrader-friendly):
      - symbol (str): e.g. 'NSE:TCS' or 'NSE_INDEX:NIFTY' (required)
      - exchange (str|None): optional override of exchange (if not in symbol)
      - timeframe (bt.TimeFrame): default bt.TimeFrame.Days
      - compression (int): default 1
      - fromdate (datetime|None): default None -> use (today - 365 days)
      - todate (datetime|None): default None -> use today
      - interval (str|None): if provided, overrides timeframe/compression mapping
      - api_key (str|None): optional override (otherwise from env)
      - host (str|None): OpenAlgo host (default env OPENALGO_API_HOST or http://127.0.0.1:5000)

    Notes:
      - This feed currently loads historical data at start() and iterates via _load().
      - Live/streaming updates can be added later by wiring a queue filled from a websocket.
    """

    lines = ("open", "high", "low", "close", "volume", "openinterest")
    params = (
        ("symbol", None),
        ("symbols", None),
        ("exchange", None),
        ("timeframe", TIMEFRAME_DAYS),
        ("compression", 1),
        ("fromdate", None),
        ("todate", None),
        ("interval", None),
        ("api_key", None),
        ("host", None),
        ("stamp_daily_at_close", True),  # If True, stamp daily bars at local market close
        ("daily_close_hhmm", "15:30"),   # Local market close time (HH:MM) in Asia/Kolkata
        # Live streaming params
        ("live", False),                 # Enable live streaming aggregation
        ("ws_url", None),                # Websocket URL (fallback to WEBSOCKET_URL env)
        ("ws_mode", 2),                  # Subscription mode (2 = Quote)
    )

    def __init__(self, **kwargs):
        # Backtrader passes params via kwargs matching self.params
        super().__init__()
        self._q: Deque[Dict[str, Any]] = deque()
        self._store: Optional[OAStore] = None  # type: ignore
        # Live streaming internals
        self._ws_thread = None
        self._ws = None
        self._ws_stop = threading.Event()
        self._lock = threading.Lock()
        self._interval = None
        self._cur_minute = None
        self._cur_bar = None
        self._live_started = False
        self._effective_symbol: Optional[str] = None

    # -------------
    # BT Lifecycle
    # -------------
    def start(self):
        super().start()

        load_dotenv()

        # Initialize store
        self._store = OAStore(  # type: ignore
            api_key=self.p.api_key or os.getenv("OPENALGO_API_KEY"),
            host=self.p.host or os.getenv("OPENALGO_API_HOST"),
        )

        # Resolve symbol from 'symbol' or 'symbols'
        self._effective_symbol = self._resolve_symbol()

        # Determine date range
        todate: datetime = self.p.todate or datetime.now(tz=timezone.utc)
        fromdate: datetime = self.p.fromdate or (todate - timedelta(days=365))

        # Determine interval
        if self.p.interval:
            interval = self.p.interval
        else:
            interval = self._store.bt_to_interval(self.p.timeframe, int(self.p.compression))  # type: ignore
        self._interval = interval

        # Fetch historical candles
        candles: List[Dict[str, Any]] = self._store.fetch_historical(  # type: ignore
            symbol=self._effective_symbol,
            start=fromdate,
            end_date=todate,
            interval=interval,
            exchange=self.p.exchange,
        )

        # Load into internal queue (convert datetimes to UTC naive for BT date2num)
        is_daily = str(interval).upper() in {"D", "1D", "DAY", "DAILY"}
        for c in candles:
            dt = c.get("datetime")
            if is_daily and self.p.stamp_daily_at_close:
                dt_naive_utc = self._daily_close_utc_naive(dt, self.p.daily_close_hhmm)
            else:
                dt_naive_utc = self._to_naive_utc(dt)
            if dt_naive_utc is None:
                continue

            self._q.append(
                {
                    "datetime": dt_naive_utc,
                    "open": float(c.get("open", 0.0) or 0.0),
                    "high": float(c.get("high", 0.0) or 0.0),
                    "low": float(c.get("low", 0.0) or 0.0),
                    "close": float(c.get("close", 0.0) or 0.0),
                    "volume": float(c.get("volume", 0.0) or 0.0),
                    "openinterest": float(c.get("openinterest", 0.0) or 0.0),
                }
            )

        # Start live mode if requested and interval is 1m
        if self.p.live and str(self._interval).lower() in {"1m", "1min", "1minute"}:
            self._start_ws()

    def stop(self):
        super().stop()
        self._q.clear()
        self._stop_ws()

    # -------------
    # Data Loading
    # -------------
    def _load(self) -> bool:
        """
        Called by Backtrader to load the next bar.
        Returns True if a bar has been loaded into the data lines, False if no more.
        """
        if not self._q:
            # In live mode, keep the engine running even if no bar is ready yet.
            # Sleep briefly and return None to indicate "no data right now".
            if self.p.live:
                time.sleep(0.5)
                return None  # type: ignore[return-value]
            return False

        bar = self._q.popleft()

        # Set datetime (as BT float)
        self.lines.datetime[0] = bt.date2num(bar["datetime"])  # type: ignore[attr-defined]

        # Set OHLCV and OI
        self.lines.open[0] = bar["open"]  # type: ignore[attr-defined]
        self.lines.high[0] = bar["high"]  # type: ignore[attr-defined]
        self.lines.low[0] = bar["low"]  # type: ignore[attr-defined]
        self.lines.close[0] = bar["close"]  # type: ignore[attr-defined]
        self.lines.volume[0] = bar["volume"]  # type: ignore[attr-defined]
        self.lines.openinterest[0] = bar["openinterest"]  # type: ignore[attr-defined]

        return True

    # -------------
    # Helpers
    # -------------
    def _resolve_symbol(self) -> str:
        """
        Determine the effective symbol to use from params 'symbol' or 'symbols'.

        Rules:
          - Provide either 'symbol' (str) or 'symbols' (str or list/tuple with exactly one item).
          - If both are provided, raise ValueError.
          - If 'symbols' has more than one item, raise ValueError instructing to create one OAData per symbol.
        """
        sym = self.p.symbol
        syms = self.p.symbols

        # Disallow both
        if sym and syms is not None:
            raise ValueError("Provide either 'symbol' or 'symbols', not both.")

        # Handle 'symbols'
        if syms is not None:
            if isinstance(syms, str):
                s = syms.strip()
                if not s:
                    raise ValueError("'symbols' provided as empty string")
                return s
            if isinstance(syms, (list, tuple)):
                if len(syms) == 0:
                    raise ValueError("'symbols' list is empty")
                if len(syms) > 1:
                    raise ValueError("OAData supports one instrument per feed. Create one OAData per symbol.")
                val = syms[0]
                if not isinstance(val, str) or not val.strip():
                    raise ValueError("Invalid symbol inside 'symbols' list")
                return val.strip()
            raise ValueError("'symbols' must be a string or a list/tuple of one string")

        # Fallback to 'symbol'
        if not sym or not isinstance(sym, str) or not sym.strip():
            raise ValueError("You must provide 'symbol' (str) or 'symbols' (list[str] with one item)")
        return sym.strip()

    @staticmethod
    def _to_naive_utc(dt: Any) -> Optional[datetime]:
        """
        Convert various datetime/timestamp types to naive UTC datetime suitable for bt.date2num.
        Supports:
          - pandas.Timestamp (tz-aware/naive)
          - datetime (tz-aware/naive)
          - string (ISO-like)
        """
        if dt is None:
            return None

        # Pandas Timestamp
        try:
            import pandas as pd  # local import
            if isinstance(dt, pd.Timestamp):
                if dt.tz is not None:
                    return dt.tz_convert("UTC").to_pydatetime().replace(tzinfo=None)
                return dt.to_pydatetime().replace(tzinfo=None)
        except Exception:
            pass

        # Python datetime
        if isinstance(dt, datetime):
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt.replace(tzinfo=None)

        # Parse string
        if isinstance(dt, str):
            try:
                # Attempt ISO parse
                parsed = datetime.fromisoformat(dt)
                if parsed.tzinfo is not None:
                    return parsed.astimezone(timezone.utc).replace(tzinfo=None)
                return parsed.replace(tzinfo=None)
            except Exception:
                return None

        return None

    @staticmethod
    def _daily_close_utc_naive(dt: Any, hhmm: str) -> Optional[datetime]:
        """
        Given a datetime-like 'dt' representing a trading day (typically in Asia/Kolkata),
        return a naive UTC datetime stamped at that day's local market close time (hh:mm) in Asia/Kolkata.
        """
        # Parse hhmm
        try:
            parts = hhmm.split(":")
            hh = int(parts[0]); mm = int(parts[1])
        except Exception:
            hh, mm = 15, 30

        # Ensure we have a timezone-aware IST datetime representing the same calendar day
        try:
            from zoneinfo import ZoneInfo  # Python 3.9+
            tz_ist = ZoneInfo("Asia/Kolkata")
        except Exception:
            try:
                import pytz  # type: ignore
                tz_ist = pytz.timezone("Asia/Kolkata")
            except Exception:
                tz_ist = None

        aware_ist = None
        # Pandas Timestamp
        try:
            import pandas as pd  # local import
            if isinstance(dt, pd.Timestamp):
                if dt.tz is None:
                    if tz_ist is not None:
                        aware_ist = dt.to_pydatetime().replace(tzinfo=timezone.utc).astimezone(tz_ist)
                    else:
                        aware_ist = dt.to_pydatetime().replace(tzinfo=None)
                else:
                    if tz_ist is not None:
                        aware_ist = dt.tz_convert(tz_ist).to_pydatetime()
                    else:
                        aware_ist = dt.to_pydatetime()
        except Exception:
            pass

        if aware_ist is None and isinstance(dt, datetime):
            if dt.tzinfo is not None:
                if tz_ist is not None:
                    aware_ist = dt.astimezone(tz_ist)
                else:
                    aware_ist = dt
            else:
                # Treat naive as UTC then convert to IST if possible
                if tz_ist is not None:
                    aware_ist = dt.replace(tzinfo=timezone.utc).astimezone(tz_ist)
                else:
                    aware_ist = dt

        if aware_ist is None:
            # Try parse string
            if isinstance(dt, str):
                try:
                    parsed = datetime.fromisoformat(dt)
                    if parsed.tzinfo is not None and tz_ist is not None:
                        aware_ist = parsed.astimezone(tz_ist)
                    elif tz_ist is not None:
                        aware_ist = parsed.replace(tzinfo=timezone.utc).astimezone(tz_ist)
                    else:
                        aware_ist = parsed
                except Exception:
                    return None

        if not isinstance(aware_ist, datetime):
            return None

        # Set to local close time on same calendar day
        local_close = aware_ist.replace(hour=hh, minute=mm, second=0, microsecond=0)

        # Convert to UTC and drop tzinfo (naive)
        return local_close.astimezone(timezone.utc).replace(tzinfo=None)

    # -------------
    # Live WebSocket - 1m Aggregation
    # -------------
    def _start_ws(self) -> None:
        ws_url = self.p.ws_url or os.getenv("WEBSOCKET_URL")
        if not ws_url:
            return
        symbol, exchange = self._split_symbol_exchange()
        try:
            WS_HUB.subscribe(ws_url=ws_url, exchange=exchange, symbol=symbol, mode=int(self.p.ws_mode), consumer=self)
        except Exception:
            # Swallow to avoid stopping Backtrader on WS issues
            pass

    def _stop_ws(self) -> None:
        try:
            ws_url = self.p.ws_url or os.getenv("WEBSOCKET_URL")
            if ws_url:
                WS_HUB.unsubscribe(ws_url=ws_url, consumer=self)
        except Exception:
            # Ensure stop never raises
            pass

    def _ws_run(self, ws_url: str) -> None:
        try:
            import websocket  # type: ignore
        except Exception:
            # websocket-client not installed
            return

        # Resolve symbol/exchange for subscription
        symbol, exchange = self._split_symbol_exchange()
        api_key = os.getenv("OPENALGO_API_KEY")

        def on_open(ws):
            # Authenticate
            try:
                if api_key:
                    ws.send(json.dumps({"action": "authenticate", "api_key": api_key}))
                # Subscribe
                ws.send(json.dumps({
                    "action": "subscribe",
                    "symbol": symbol,
                    "exchange": exchange,
                    "mode": int(self.p.ws_mode),
                }))
                # Notify LIVE immediately (even before first tick) so Cerebro doesn't consider feed finished
                if not self._live_started and hasattr(self, "put_notification"):
                    try:
                        self.put_notification(self.LIVE)  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    self._live_started = True
            except Exception as e:
                print(f"Exception in on_open : {e}")
                pass

        def on_message(ws, message):
            # Exit early if stopping
            if self._ws_stop.is_set():
                try:
                    ws.close()
                except Exception:
                    pass
                return

            try:
                msg = json.loads(message)
                print(msg)
            except Exception:
                return

            if isinstance(msg, dict) and msg.get("type") == "ping":
                try:
                    ws.send(json.dumps({"type": "pong"}))
                except Exception:
                    pass
                return

            # Extract price and timestamp
            price = self._parse_price(msg)
            if price is None:
                return

            # Timestamp extraction
            ts = None
            # Common fields
            for k in ("timestamp", "ts", "time", "t"):
                if isinstance(msg.get(k), (int, float, str)):
                    ts = msg.get(k)
                    break
            if ts is None and isinstance(msg.get("data"), dict):
                d = msg["data"]
                for k in ("timestamp", "ts", "time", "t"):
                    if isinstance(d.get(k), (int, float, str)):
                        ts = d.get(k)
                        break

            dt_utc = None
            try:
                # int epoch seconds or ms
                if isinstance(ts, (int, float)):
                    val = float(ts)
                    if val > 1e12:  # ms
                        dt_utc = datetime.fromtimestamp(val / 1000.0, tz=timezone.utc)
                    else:
                        dt_utc = datetime.fromtimestamp(val, tz=timezone.utc)
                elif isinstance(ts, str):
                    # Try ISO string
                    try:
                        parsed = datetime.fromisoformat(ts)
                        dt_utc = parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                    except Exception:
                        dt_utc = datetime.now(tz=timezone.utc)
                else:
                    dt_utc = datetime.now(tz=timezone.utc)
            except Exception as e:
                dt_utc = datetime.now(tz=timezone.utc)
                print(f"Exception in on_message : {e}")

            self._handle_tick(price, dt_utc)

        def on_error(ws, error):
            # Backoff a bit on errors
            time.sleep(1.0)
            print("on_error() got called")

        def on_close(ws, close_status_code, close_msg):
            print(f"on_close called with {close_status_code} {close_msg}")
            pass

        while not self._ws_stop.is_set():
            try:
                self._ws = websocket.WebSocketApp(
                    ws_url,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                self._ws.run_forever()
            except Exception:
                pass
            finally:
                self._ws = None
            # Reconnect delay
            if not self._ws_stop.is_set():
                time.sleep(1.0)

    def _handle_tick(self, price: float, dt_utc: datetime) -> None:
        # Convert UTC -> IST and floor to minute
        try:
            from zoneinfo import ZoneInfo
            tz_ist = ZoneInfo("Asia/Kolkata")
        except Exception:
            tz_ist = None

        if tz_ist is not None:
            dt_ist = dt_utc.astimezone(tz_ist)
        else:
            # Fallback: approximate IST as +5:30 (only for flooring purpose)
            dt_ist = dt_utc + timedelta(hours=5, minutes=30)

        minute_ist = dt_ist.replace(second=0, microsecond=0)

        with self._lock:
            # If minute changed, finalize previous bar
            if self._cur_minute is not None and minute_ist != self._cur_minute and self._cur_bar:
                self._finalize_minute(self._cur_minute)

            # Initialize bar if needed
            if self._cur_minute != minute_ist or not self._cur_bar:
                self._cur_minute = minute_ist
                self._cur_bar = {
                    "open": float(price),
                    "high": float(price),
                    "low": float(price),
                    "close": float(price),
                    "volume": 0.0,
                    "openinterest": 0.0,
                }
                # Notify LIVE once
                if not self._live_started and hasattr(self, "put_notification"):
                    try:
                        self.put_notification(self.LIVE)  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    self._live_started = True
            else:
                # Update bar
                self._cur_bar["close"] = float(price)
                self._cur_bar["high"] = max(self._cur_bar["high"], float(price))
                self._cur_bar["low"] = min(self._cur_bar["low"], float(price))

    def _finalize_minute(self, minute_ist: datetime) -> None:
        # Convert minute start in IST to naive UTC for Backtrader
        dt_naive_utc = minute_ist.astimezone(timezone.utc).replace(tzinfo=None)
        bar = {
            "datetime": dt_naive_utc,
            "open": self._cur_bar.get("open", 0.0) if self._cur_bar else 0.0,
            "high": self._cur_bar.get("high", 0.0) if self._cur_bar else 0.0,
            "low": self._cur_bar.get("low", 0.0) if self._cur_bar else 0.0,
            "close": self._cur_bar.get("close", 0.0) if self._cur_bar else 0.0,
            "volume": self._cur_bar.get("volume", 0.0) if self._cur_bar else 0.0,
            "openinterest": self._cur_bar.get("openinterest", 0.0) if self._cur_bar else 0.0,
        }
        self._q.append(bar)
        # Reset current bar
        self._cur_bar = None

    @staticmethod
    def _parse_price(msg: Any) -> Optional[float]:
        if not isinstance(msg, dict):
            return None
        # Try direct fields
        for k in ("ltp", "last_price", "price", "close", "p"):
            v = msg.get(k)
            if isinstance(v, (int, float)):
                return float(v)
            # Some providers send as str
            if isinstance(v, str):
                try:
                    return float(v)
                except Exception:
                    pass
        # Try nested 'data'
        d = msg.get("data")
        if isinstance(d, dict):
            for k in ("ltp", "last_price", "price", "close", "p"):
                v = d.get(k)
                if isinstance(v, (int, float)):
                    return float(v)
                if isinstance(v, str):
                    try:
                        return float(v)
                    except Exception:
                        pass
        return None

    def _split_symbol_exchange(self) -> tuple[str, str]:
        """
        Returns (symbol, exchange) for websocket subscription.
        Accepts either 'symbol' or 'symbols' (length 1). If like 'NSE:RELIANCE', exchange='NSE', symbol='RELIANCE' unless self.p.exchange overrides.
        """
        sym = (self._effective_symbol or self.p.symbol or "")
        exch = self.p.exchange
        if isinstance(sym, str) and ":" in sym:
            parts = sym.split(":", 1)
            if len(parts) == 2:
                exch = exch or parts[0]
                sym = parts[1]
        return sym, (exch or "NSE")


__all__ = ["OAData"]
