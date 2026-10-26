# Five-minute SMA crossover simulation

## What it does

`sma_crossover_5m.py` buys a fixed quantity when SMA(5) crosses above SMA(20),
and closes the long on the reverse crossover. It uses market orders and keeps
one pending order. Periods, quantity and starting cash are configurable.

The default source is deterministic synthetic five-minute OHLCV in weekday
09:15-15:25 Asia/Kolkata sessions. Prices deliberately oscillate to exercise
entries and exits; these are not market observations or an exchange calendar.
The example reports UTC timestamps, completed executions, closed trades, cash,
marked-to-market value, final position, pending order and maximum drawdown.

## Run locally

From the repository root, with Python 3.12+:

```bash
uv sync --extra dev
uv run python examples/sma_crossover_5m.py
uv run python examples/sma_crossover_5m.py --broker oa-sim
```

No credentials, server, plotting package or custom strategy helpers are required.
Backtrader is installed by the repository development group, not a mandatory
runtime dependency of the library. Pandas comes with the OpenAlgo SDK dependency.

```bash
uv run python examples/sma_crossover_5m.py --broker backtrader --bars 600 --quantity 10 --cash 100000 --json
```

`--json` includes signal times/prices, execution times/prices and order statuses,
so you can compare exactly how the brokers filled the same signals.

## Execution models and limits

- `backtrader` (default): ordinary Backtrader simulated market fills at the next
  available bar's open. Prefer this for evaluating a strategy's backtest.
- `oa-sim`: `OABroker(mode="backtest", simulate_fills=True, use_funds=False)` with
  an inert store. Orders fill immediately at the current close. This exercises
  integration mechanics, not realistic execution, buying-power or margin rules.
- Both use zero fees and slippage. No paper/live broker modes, websocket, analyzer,
  account queries, upstream placement or cancellation are exposed. Historical
  credentials are never attached to either broker.
- No session-end or dataset-end forced liquidation is performed. An open position
  contributes to value; a final pending next-bar order is reported, not fabricated
  as a fill. SMA warmup or no crossovers can produce no trades.
- An order created on the last bar may never deliver its notification to the
  strategy. `pending_order` contains only an alive broker order; `undelivered_order`
  shows the final order's broker status and executed size/price. In `oa-sim` that
  can already be `Completed` and reflected in cash/position, while `fills` and the
  trade/drawdown analyzers include only delivered notifications. Text output warns
  when this occurs. No synthetic extra bar or manual notification flush is added.
- Realistic commissions, spreads, liquidity, exchange calendars, intraday square-off,
  stops, position sizing and out-of-sample evaluation are outside this diagnostic.

## Optional OpenAlgo historical prices

Export `OPENALGO_API_KEY` securely and optionally `OPENALGO_API_HOST` for your
intended deployment. The default host is `http://127.0.0.1:5000`. This example
never loads `.env` files. Dates must be explicit; pick a completed historical
range that provides at least `slow + 2` candles (22 with the default periods).

```bash
uv run python examples/sma_crossover_5m.py --source openalgo --symbol NSE:RELIANCE --start 2026-08-03 --end 2026-08-14 --broker backtrader
uv run python examples/sma_crossover_5m.py --source openalgo --symbol NSE:RELIANCE --start 2026-08-03 --end 2026-08-14 --broker oa-sim
```

Only `/api/v1/history` is requested with interval `5m`, using OpenAlgoAdapter's
REST normalization. The target must support native `5m`; this example does not
silently fall back to `1m`, synthetic prices or a different interval. These
commands fetch market history but orders remain entirely local simulations.

The adapter is constructed with `load_env=False` and an inert SDK client for
this REST-only path. The default adapter behavior elsewhere remains unchanged.
Data is sorted and converted to UTC-naive before PandasData, checked for duplicate
or misaligned timestamps and invalid OHLCV. Overnight/weekend gaps are allowed.
This is not a history completeness, timezone-contract or live compatibility probe.

## Offline verification

```bash
uv run pytest -q tests/test_sma_crossover_example.py
```

Tests use real Cerebro runs for both local brokers, verify fill timing and cash,
and inject fake history transport. They block sockets and dotenv and verify the
example does not create an SDK client. No actual OpenAlgo server is contacted.
