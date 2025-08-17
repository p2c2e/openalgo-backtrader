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

## Requirements

- Python 3.8+
- [python-dotenv](https://pypi.org/project/python-dotenv/)
- [backtrader](https://www.backtrader.com/) (user must install)
- [openalgo](https://github.com/openalgo/openalgo-python) (user must install)

## License

See [LICENSE](LICENSE).
