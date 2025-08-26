# openalgo-backtrader

Backtrader integration for OpenAlgo (India) - stores, feeds, and brokers.

## Installation

```bash
uv pip install openalgo-backtrader
```

## Usage

Import the package in your code as follows:

```python
from openalgo_bt.stores.oa import OAStore
from openalgo_bt.feeds.oa import OAData
```

## Project Structure

- `openalgo_bt/` - Main package
  - `stores/oa.py` - OAStore for OpenAlgo API
  - `feeds/oa.py` - OAData for Backtrader feeds
  - `brokers/oabroker.py` - OABroker for Backtrader brokers


### Sample Store/Broker Creation
Here is how to setup a Store/Broker (example tested with Zerodha backend at OpenAlgo). Both blocks are for "LIVE" setup
```python
from openalgo_bt.stores.oa import OAStore
store = OAStore()
broker = store.getbroker(
    product="MIS", 
    strategy="Live Consistent Trend Bracket", 
    debug=True,
    simulate_fills=False,  # Use real broker for live trading
    use_funds=False        # Use local cash management
)
cerebro.setbroker(broker)
```
And to setup a Data (or multiple for that matter)

```python
data = OAData(
  symbol=symbol,
  interval="1m",                   # Explicit OpenAlgo interval to bypass timeframe/compression mapping
  compression=1,                   # 1-minute bars
  fromdate=datetime.now() - timedelta(days=1),  # Some historical data for warmup
  live=True,                       # Enable live streaming
  ws_url=ws_url,
  ws_mode=2,                       # Quote mode
)
cerebro.adddata(data, name=symbol)
```

## Using higher compression values (intraday aggregation)

When your backend only provides 1-minute data, OAData can now deliver higher intraday compressions by resampling locally:

- Historical:
  - If you request Minutes/Hours with compression>1 and the OpenAlgo backend does not support that interval, OAData fetches 1m candles and aggregates them into N-minute buckets.
- Live:
  - Websocket ticks are aggregated into the requested bucket size on the fly.

Notes:
- Bucket alignment is done in Asia/Kolkata time by flooring to the nearest multiple of the bucket size.
- Volume comes from per-tick last_quantity when available, or from deltas of cumulative session volume otherwise.
- If OpenAlgo supports the requested interval natively (e.g. 5m), OAData will use it. If not, it will fallback to 1m + local aggregation transparently.

Examples:
```python
# Historical 5-minute bars (local 1m->5m if backend interval unsupported)
import backtrader as bt
from datetime import datetime, timedelta
from openalgo_bt.feeds.oa import OAData

TF_MINUTES = getattr(getattr(bt, "TimeFrame", object), "Minutes", 1)

data = OAData(
    symbol="NSE:RELIANCE",
    timeframe=TF_MINUTES,
    compression=5,
    fromdate=datetime.now() - timedelta(days=5),
    todate=datetime.now(),
)
```

```python
# Live 15-minute bars (aggregated locally from 1m ticks)
import os
import backtrader as bt
from datetime import datetime
from openalgo_bt.feeds.oa import OAData

TF_MINUTES = getattr(getattr(bt, "TimeFrame", object), "Minutes", 1)

data = OAData(
    symbol="NSE:RELIANCE",
    timeframe=TF_MINUTES,
    compression=15,
    fromdate=datetime(2025, 8, 8),   # optional historical warmup
    live=True,
    ws_url=os.getenv("WEBSOCKET_URL"),
    ws_mode=2,  # Quote mode
)
```

Tip: Some editors’ type stubs don’t expose bt.TimeFrame.Minutes; use:
TF_MINUTES = getattr(getattr(bt, "TimeFrame", object), "Minutes", 1)

## Requirements

- Python 3.8+
- [python-dotenv](https://pypi.org/project/python-dotenv/)
- [backtrader](https://www.backtrader.com/) (user must install)
- [openalgo](https://github.com/openalgo/openalgo-python) (user must install)

## License

See [LICENSE](LICENSE).
