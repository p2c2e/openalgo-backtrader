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

## Requirements

- Python 3.8+
- [python-dotenv](https://pypi.org/project/python-dotenv/)
- [backtrader](https://www.backtrader.com/) (user must install)
- [openalgo](https://github.com/openalgo/openalgo-python) (user must install)

## License

See [LICENSE](LICENSE).
