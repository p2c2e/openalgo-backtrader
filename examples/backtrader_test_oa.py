import os
import matplotlib
matplotlib.use("Agg")  # headless backend

import backtrader as bt
from datetime import datetime, timezone
from openalgo_bt.feeds.oa import OAData

# Avoid static analysis errors for bt.TimeFrame.Minutes while remaining runtime-correct
TF_MINUTES = getattr(getattr(bt, "TimeFrame", object), "Minutes", 1)


class PrintBars(bt.Strategy):
    def __init__(self):
        self.bar_count = 0

    def next(self):
        self.bar_count += 1
        # Print first and last few bars
        if self.bar_count <= 2 or len(self.data) - self.bar_count <= 2:
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
            print(f"{dt_ist.strftime('%Y-%m-%d %H:%M')} IST O:{self.data.open[0]} H:{self.data.high[0]} L:{self.data.low[0]} C:{self.data.close[0]} V:{self.data.volume[0]}")

    def stop(self):
        print(f"Total bars loaded: {self.bar_count}")


def main():
    # Ensure OpenAlgo environment is configured
    # Required:
    #   - OPENALGO_API_KEY in environment or .env
    # Optional:
    #   - OPENALGO_API_HOST (defaults to http://127.0.0.1:5000)
    if not os.getenv("OPENALGO_API_KEY"):
        print("WARNING: OPENALGO_API_KEY not found in environment. Set it in your .env or shell.")

    cerebro = bt.Cerebro()

    data = OAData(
        symbol="NSE:JSWENERGY",
        timeframe=TF_MINUTES,
        compression=5,
        fromdate=datetime(2025, 1, 8),
        todate=datetime(2025, 1, 10),
        # interval="1m",  # optionally override timeframe/compression mapping
        # api_key="...",  # optionally override env
        # host="http://127.0.0.1:5000",
    )
    cerebro.adddata(data)
    cerebro.addstrategy(PrintBars)

    cerebro.run()

    # Save a plot as PNG
    cerebro.plot(style="candlestick", iplot=False, volume=True, savefig=True, figfilename="oa_feed_plot.png")


if __name__ == "__main__":
    main()
