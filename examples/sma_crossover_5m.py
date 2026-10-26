"""Local-only SMA crossover diagnostic; see sma_crossover_5m.md for usage."""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import backtrader as bt
import pandas as pd

from openalgo_bt.adapters.openalgo import (
    OpenAlgoAdapter,
    OpenAlgoAPIError,
    normalize_history_candles,
)
from openalgo_bt.brokers.oabroker import OABroker


class SimulationStore:
    """Never expose a historical client or routing adapter to the broker."""

    def get_client(self):
        return None


class SMACrossover(bt.Strategy):
    """Long-only crossover with one pending market order at a time."""

    params = dict(fast=5, slow=20, quantity=1)

    def __init__(self):
        fast = bt.indicators.SMA(self.data.close, period=self.p.fast)
        slow = bt.indicators.SMA(self.data.close, period=self.p.slow)
        self.cross = bt.indicators.CrossOver(fast, slow)
        self.pending = None
        self.signals = []
        self.fills = []
        self.statuses = []

    def next(self):
        if self.pending is not None:
            return
        if not self.position and self.cross[0] > 0:
            self.pending = self.buy(size=self.p.quantity, exectype=bt.Order.Market)
        elif self.position and self.cross[0] < 0:
            self.pending = self.close(exectype=bt.Order.Market)
        else:
            return
        if self.pending is not None:
            self.signals.append({
                "ref": self.pending.ref,
                "side": "BUY" if self.pending.isbuy() else "SELL",
                "datetime_utc": self.data.datetime.datetime(0).isoformat(),
                "close": float(self.data.close[0]),
            })

    def notify_order(self, order):
        self.statuses.append({"ref": order.ref, "status": order.getstatusname()})
        if order.status == order.Completed:
            self.fills.append({
                "ref": order.ref,
                "side": "BUY" if order.isbuy() else "SELL",
                "datetime_utc": bt.num2date(order.executed.dt).isoformat(),
                "price": float(order.executed.price),
                "size": float(order.executed.size),
            })
        if order.status in (
            order.Completed, order.Canceled, order.Margin, order.Rejected, order.Expired,
        ) and self.pending is not None and order.ref == self.pending.ref:
            self.pending = None


def synthetic_candles(bars: int = 300) -> pd.DataFrame:
    """Generate oscillating prices in weekday IST sessions, indexed UTC-naive."""
    if isinstance(bars, bool) or not isinstance(bars, int) or bars <= 0:
        raise ValueError("bars must be a positive integer")
    session = date(2026, 1, 5)
    ist = ZoneInfo("Asia/Kolkata")
    rows = []
    for i in range(bars):
        if i and i % 75 == 0:
            session += timedelta(days=1)
            while session.weekday() >= 5:
                session += timedelta(days=1)
        local = datetime.combine(session, time(9, 15), tzinfo=ist)
        local += timedelta(minutes=5 * (i % 75))
        close = 100.0 - 10.0 * math.sin(2.0 * math.pi * i / 60.0)
        # Separate open/close prices make broker execution timing observable.
        opening = close + 0.25
        rows.append({
            "datetime": local.astimezone(timezone.utc).replace(tzinfo=None),
            "open": opening, "high": opening + 0.5, "low": close - 0.5,
            "close": close, "volume": 1000.0,
        })
    return pd.DataFrame(rows).set_index("datetime")


def historical_candles(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Read native 5m REST history only, using exported credentials, not dotenv."""
    if not symbol.strip() or ":" not in symbol:
        raise ValueError("symbol must be EXCHANGE:SYMBOL, for example NSE:RELIANCE")
    exchange, ticker = symbol.split(":", 1)
    if not exchange.strip() or not ticker.strip():
        raise ValueError("symbol must contain a nonempty exchange and ticker")
    if not start or not end:
        raise ValueError("OpenAlgo history requires --start and --end (YYYY-MM-DD)")
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError("start must not be after end")
    key = os.getenv("OPENALGO_API_KEY", "").strip()
    if not key:
        raise ValueError("Export OPENALGO_API_KEY before requesting OpenAlgo history")
    adapter = OpenAlgoAdapter(
        api_key=key,
        host=os.getenv("OPENALGO_API_HOST") or OpenAlgoAdapter.DEFAULT_HOST,
        client=object(),
        load_env=False,
    )
    candles = normalize_history_candles(adapter.history(
        symbol=ticker, exchange=exchange, interval="5m", start_date=start, end_date=end,
    ))
    if not candles:
        raise ValueError("OpenAlgo returned no candles for the requested dates")
    return pd.DataFrame(candles).set_index("datetime")


def validate_candles(frame: pd.DataFrame, slow: int) -> pd.DataFrame:
    """Reject invalid bars rather than silently running a mislabeled backtest."""
    frame = frame.copy()
    required = ["open", "high", "low", "close", "volume"]
    if any(column not in frame.columns for column in required):
        raise ValueError("candles require open, high, low, close and volume")
    if len(frame) < slow + 2:
        raise ValueError("need at least slow + 2 candles for crossover and next-bar execution")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.hasnans:
        raise ValueError("candles require a valid DatetimeIndex")
    if frame.index.tz is not None:
        frame.index = frame.index.tz_convert("UTC").tz_localize(None)
    frame = frame.sort_index()
    if frame.index.has_duplicates:
        raise ValueError("duplicate candle timestamps are not supported")
    if any(ts.second or ts.microsecond or ts.nanosecond or ts.minute % 5 for ts in frame.index):
        raise ValueError("candles must be aligned to five-minute boundaries")
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if not all(math.isfinite(value) for value in frame[column]):
            raise ValueError("candle values must be finite")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive")
    if (frame["volume"] < 0).any():
        raise ValueError("volume must be non-negative")
    if ((frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any()
            or (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any()):
        raise ValueError("inconsistent OHLC range")
    return frame


def run_simulation(
    frame: pd.DataFrame, *, broker: str = "backtrader", cash: float = 100_000.0,
    fast: int = 5, slow: int = 20, quantity: int = 1, symbol: str = "NSE:SYNTHETIC",
) -> dict:
    """Run one historical feed with a local broker and return an auditable report."""
    if broker not in {"backtrader", "oa-sim"}:
        raise ValueError("broker must be backtrader or oa-sim; upstream routing is unavailable")
    if not math.isfinite(cash) or cash <= 0:
        raise ValueError("cash must be finite and positive")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (fast, slow, quantity)):
        raise ValueError("periods and quantity must be integers")
    if not 0 < fast < slow or quantity <= 0:
        raise ValueError("require 0 < fast < slow and positive quantity")
    frame = validate_candles(frame, slow)
    cerebro = bt.Cerebro(stdstats=False)
    if broker == "oa-sim":
        cerebro.setbroker(OABroker(
            store=SimulationStore(), mode="backtest", allow_live=False,
            simulate_fills=True, use_funds=False, cash_fallback=cash,
        ))
    else:
        cerebro.broker.setcash(cash)
        cerebro.broker.set_coc(False)
    cerebro.broker.setcommission(commission=0.0)
    cerebro.adddata(bt.feeds.PandasData(
        dataname=frame, timeframe=bt.TimeFrame.Minutes, compression=5,
    ), name=symbol)
    cerebro.addstrategy(SMACrossover, fast=fast, slow=slow, quantity=quantity)
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    strategy = cerebro.run()[0]
    trades = strategy.analyzers.trades.get_analysis()
    drawdown = strategy.analyzers.drawdown.get_analysis()
    # Strategy/analyzer notifications may lag immediate fills on the final bar.
    final_order = strategy.pending
    final_snapshot = None if final_order is None else {
        "ref": final_order.ref, "status": final_order.getstatusname(),
        "executed_size": float(final_order.executed.size),
        "executed_price": float(final_order.executed.price),
    }
    return {
        "broker": broker, "symbol": symbol, "bars": len(frame),
        "fill_model": "next_bar_open" if broker == "backtrader" else "immediate_current_close",
        "starting_cash": cash, "cash": cerebro.broker.getcash(),
        "value": cerebro.broker.getvalue(), "position": strategy.position.size,
        "closed_trades": trades.get("total", {}).get("closed", 0),
        "max_drawdown_pct": drawdown.get("max", {}).get("drawdown", 0.0),
        "pending_order": final_order.ref if final_order is not None and final_order.alive() else None,
        "undelivered_order": final_snapshot,
        "signals": strategy.signals, "fills": strategy.fills, "statuses": strategy.statuses,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker", choices=("backtrader", "oa-sim"), default="backtrader")
    parser.add_argument("--source", choices=("synthetic", "openalgo"), default="synthetic")
    parser.add_argument("--symbol", default="NSE:RELIANCE", help="OpenAlgo history symbol")
    parser.add_argument("--start", help="OpenAlgo history start, YYYY-MM-DD")
    parser.add_argument("--end", help="OpenAlgo history end, YYYY-MM-DD")
    parser.add_argument("--bars", type=int, default=300, help="synthetic bar count")
    parser.add_argument("--fast", type=int, default=5)
    parser.add_argument("--slow", type=int, default=20)
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--cash", type=float, default=100_000.0)
    parser.add_argument("--json", action="store_true", help="include signals, fills and statuses")
    args = parser.parse_args(argv)
    if args.source == "synthetic" and (args.start or args.end):
        parser.error("--start and --end require --source openalgo")
    try:
        frame = (synthetic_candles(args.bars) if args.source == "synthetic"
                 else historical_candles(args.symbol, args.start, args.end))
        report = run_simulation(
            frame, broker=args.broker, cash=args.cash, fast=args.fast, slow=args.slow,
            quantity=args.quantity,
            symbol="NSE:SYNTHETIC" if args.source == "synthetic" else args.symbol,
        )
    except OpenAlgoAPIError:
        parser.error("OpenAlgo history request failed; check target, credentials and native 5m support")
    except ValueError as exc:
        parser.error(str(exc))
    report["source"] = args.source
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("LOCAL SIMULATION ONLY - no upstream orders, zero fees/slippage")
        print(f"Source: {args.source}; broker: {args.broker}; fills: {report['fill_model']}")
        if args.broker == "oa-sim":
            print("OABroker immediate fills are for integration checks, not realistic execution.")
        for fill in report["fills"]:
            print(f"{fill['datetime_utc']} UTC {fill['side']} "
                  f"size={fill['size']:g} price={fill['price']:.4f}")
        print(f"Bars: {report['bars']}; completed executions: {len(report['fills'])}; "
              f"closed trades: {report['closed_trades']}")
        print(f"Cash: {report['cash']:.2f}; value: {report['value']:.2f}; "
              f"position: {report['position']:g}; pending order: {report['pending_order']}")
        if report["undelivered_order"] is not None:
            snapshot = report["undelivered_order"]
            print(f"Final order awaiting strategy notification: {snapshot['status']}; "
                  f"executed size={snapshot['executed_size']:g}, "
                  f"price={snapshot['executed_price']:.4f}. "
                  "Trade/drawdown analyzers include delivered notifications only.")
        print(f"Maximum drawdown: {report['max_drawdown_pct']:.4f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
