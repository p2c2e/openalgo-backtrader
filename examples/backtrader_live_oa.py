import os
import sys
import signal
import time
import matplotlib
matplotlib.use("Agg")  # headless backend for safety

import backtrader as bt
from datetime import datetime, timezone
from bt.feeds.oa import OAData


class PrintLive(bt.Strategy):
    def __init__(self):
        self.livemode = False
        self.count = 0

    def notify_data(self, data, status, *args, **kwargs):
        # Backtrader will call this with statuses like LIVE, DELAYED, DISCONNECTED, etc.
        self.livemode = status == data.LIVE
        print(f"[notify_data] status={status} ({'LIVE' if self.livemode else 'NOT LIVE'})")

    def next(self):
        self.count += 1
        dt_utc = bt.num2date(self.data.datetime[0])
        # Convert to IST for display
        try:
            from zoneinfo import ZoneInfo
            dt_ist = dt_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
        except Exception:
            try:
                import pytz  # type: ignore
                dt_ist = pytz.UTC.localize(dt_utc).astimezone(pytz.timezone("Asia/Kolkata"))
            except Exception:
                dt_ist = dt_utc
        print(
            f"[bar {self.count:05d}] {dt_ist.strftime('%Y-%m-%d %H:%M')} IST "
            f"O:{self.data.open[0]:.2f} H:{self.data.high[0]:.2f} "
            f"L:{self.data.low[0]:.2f} C:{self.data.close[0]:.2f} V:{self.data.volume[0]:.2f}"
        )


def main():
    symbol = os.getenv("OA_LIVE_SYMBOL", "NSE:RELIANCE")  # override via env if desired
    ws_url = os.getenv("WEBSOCKET_URL")  # e.g. wss://...
    api_key = os.getenv("OPENALGO_API_KEY")
    if not api_key:
        print("ERROR: OPENALGO_API_KEY not set in environment. Add it to your .env or shell.")
        sys.exit(1)
    if not ws_url:
        print("WARNING: WEBSOCKET_URL not set; live streaming will not start unless provided via OAData(ws_url=...).")

    cerebro = bt.Cerebro()

    data = OAData(
        symbol=symbol,
        timeframe=bt.TimeFrame.Minutes,
        compression=1,             # 1-minute bars
        # Historical warmup range (optional). If omitted, feed defaults to last 365 days.
        fromdate=datetime(2025, 8, 8),
        # todate=...,
        live=True,                 # enable live streaming aggregation
        ws_url=ws_url,             # or pass explicitly; falls back to WEBSOCKET_URL env var
        ws_mode=2,                 # Quote mode
        # If you want to force daily stamping behavior (not used for 1m)
        # stamp_daily_at_close=True,
        # daily_close_hhmm="15:30",
    )

    cerebro.adddata(data)
    cerebro.addstrategy(PrintLive)

    # Graceful shutdown on Ctrl+C
    def handle_sigint(signum, frame):
        print("SIGINT received, shutting down...")
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, handle_sigint)

    try:
        # For live feeds, disable runonce/preload so Backtrader doesn't pre-consume the queue
        # and exit before the websocket thread can push new bars.
        cerebro.run(runonce=False, preload=False)
    except KeyboardInterrupt:
        pass

    # Wait for 15 seconds without exiting to observe incoming bars
    # try:
    #     for i in range(15, 0, -1):
    #         print(f"Waiting... {i}s", end="\r", flush=True)
    #         time.sleep(1)
    #     print()
    # except KeyboardInterrupt:
    #     pass
    # No plotting for live
    print("Exiting live run.")


if __name__ == "__main__":
    main()
