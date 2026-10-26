# Five-minute simulation example

## Goal

Provide a small SMA crossover strategy to verify local Backtrader execution before
introducing the custom commissions, analyzers and history helpers in
`openalgo_strategies`. This example is a diagnostic, not a trading recommendation.

## Decisions

- Default to deterministic synthetic OHLCV at five-minute intervals during weekday
  India-market sessions. No credential, dotenv, websocket or network access is needed.
- Buy a fixed quantity on SMA(5)/SMA(20) upward crossover; close the long on the
  reverse crossover. Keep one pending market order and clear it on terminal notice,
  including margin rejection. Do not enter shorts or force a final liquidation.
- Offer `backtrader` (next-bar open) and `oa-sim` (immediate current close) brokers.
  Use an inert store for OABroker with explicit backtest/simulation/no-funds flags.
  Never attach the historical adapter to the broker. No paper/live CLI modes exist.
- Keep both modes at zero fees/slippage for an execution comparison. OABroker's
  immediate fills are intentionally not a realistic backtest execution model.
- Optional `openalgo` data requires explicit start/end dates and exported credentials.
  Fetch native `5m` history through OpenAlgoAdapter and normalize before PandasData.
  No implicit 1m fallback, OAData websocket, or OAStore initialization is needed.
- Add a default-preserving `load_env=False` adapter option so the example can avoid
  implicit dotenv reads. Supply an inert client because this path uses REST only.
- Validate nonempty, sufficient, finite OHLCV data, UTC-naive ordered timestamps and
  five-minute alignment before execution. Preserve legitimate session gaps.
- Report closed trades, executions, cash, marked-to-market value, open position,
  pending order and maximum drawdown. Mark open trades instead of inventing exits.
- At dataset end distinguish an alive pending order from a completed immediate
  fill awaiting delivery. Report the original order snapshot without flushing
  notifications, inventing a next bar, or rewriting analyzer results.
- Do not change the package version, submit live probes, or commit without request.

## Work plan

- [x] Add adapter dotenv opt-out with backward-compatibility tests.
- [x] Add example and matching scriptinfo with commands for both brokers/data sources.
- [x] Add offline real-Cerebro tests for signal/fill timing, reporting and input errors.
- [x] Exercise history with fake transport and block network/dotenv in synthetic tests.
- [x] Run the full offline suite and both synthetic CLI modes; update tracking docs.
