# openalgo-backtrader

Backtrader integration for OpenAlgo (India) - stores, feeds, and brokers.

## Installation

```bash
uv pip install openalgo-backtrader
uv pip install backtrader  # Required to run Backtrader strategies
```

## Usage

Import the package in your code as follows:

```python
from openalgo_bt.stores.oa import OAStore
from openalgo_bt.feeds.oa import OAData
```

## Local five-minute simulation

The examples are repository-only and are not installed by the wheel or included
in the source distribution. Clone the repository before running these commands:

```bash
git clone https://github.com/p2c2e/openalgo-backtrader.git
cd openalgo-backtrader
```

Try the self-contained SMA crossover example before adding custom strategy helpers:

```bash
uv sync --extra dev
uv run python examples/sma_crossover_5m.py
uv run python examples/sma_crossover_5m.py --broker oa-sim
```

Both modes keep orders local. The default uses synthetic data and ordinary
Backtrader next-bar-open fills; `oa-sim` tests OABroker's immediate current-close
fills, not realistic execution. Read [the example guide](examples/sma_crossover_5m.md)
for optional read-only OpenAlgo history, JSON reports and limitations.

## Current modernization plan

This repo is being refreshed because the OpenAlgo API and broker integrations may have evolved since the original Backtrader integration was written.

Tracking documents:

- `docs/updatation-plan.md` - overall plan, phases, status, and release milestones
- `docs/compatibility.md` - compatibility matrix for OpenAlgo, Backtrader, Python, intervals, websocket, and broker features
- `docs/task-log.md` - running log of intermediate tasks and findings

Status is tracked with `[todo]`, `[doing]`, `[done]`, `[deferred]`, and `[blocked]` markers.

## Project Structure

- `openalgo_bt/` - Main package
  - `stores/oa.py` - OAStore for OpenAlgo API
  - `feeds/oa.py` - OAData for Backtrader feeds
  - `brokers/oabroker.py` - OABroker for Backtrader brokers


### Sample Store/Broker Creation

This development release is not validated for production live trading. Offline
broker tests do not verify a deployed OpenAlgo order contract, and websocket
behavior remains experimental.

For local integration simulation with an existing `cerebro` instance and exported
OpenAlgo configuration:

```python
from openalgo_bt.stores.oa import OAStore

store = OAStore()
broker = store.getbroker(
    product="MIS",
    strategy="Local Integration Simulation",
    mode="backtest",
    simulate_fills=True,
    use_funds=False,
)
cerebro.setbroker(broker)
```

Simulation fills immediately at the current close; it is not a realistic execution
model. Store construction still requires an API key. Use the self-contained
synthetic example above when no OpenAlgo configuration is available.

Non-simulated backtest orders are rejected. Paper routing requires `mode="paper"`
and an OpenAlgo analyzer response identifying an analyze/paper target with
`analyze_mode` enabled. Live routing additionally requires `mode="live"`,
`allow_live=True`, `OPENALGO_BT_ALLOW_LIVE=1`, and a matching live analyzer state.
These checks are safeguards, not evidence of live-trading readiness. Process
restart recovery and atomic account reconciliation are unsupported.

The following live-data configuration is illustrative only; verify websocket
authentication and subscriptions against your intended deployment before use.

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

Tip: Some editors' type stubs don't expose bt.TimeFrame.Minutes; use:
TF_MINUTES = getattr(getattr(bt, "TimeFrame", object), "Minutes", 1)

## Requirements

- Python 3.12+
- [python-dotenv](https://pypi.org/project/python-dotenv/)
- [backtrader](https://www.backtrader.com/) (user must install)
- [openalgo](https://github.com/openalgo/openalgo-python) (installed automatically)

Development test command:

```bash
uv run pytest
```

## License

See [LICENSE](LICENSE).
