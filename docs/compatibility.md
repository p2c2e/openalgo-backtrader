# Compatibility Matrix

This document records tested compatibility between `openalgo-backtrader`, OpenAlgo server versions, OpenAlgo Python client versions, Backtrader, and broker backends.

Status legend:

- `[todo]` Not tested yet
- `[done]` Tested and documented
- `[blocked]` Needs live OpenAlgo or broker access
- `[deferred]` Not part of the current milestone

## Package Baseline

| Item | Current value | Status | Notes |
| ---- | ------------- | ------ | ----- |
| Package version | `0.1.12` | [done] | From `pyproject.toml` |
| Python support | `>=3.12` | [done] | Defined in `pyproject.toml` |
| OpenAlgo Python dependency | `openalgo>=1.0.29` | [done] | Actual tested latest client still to be verified |
| Backtrader dependency | Optional / user-installed | [done] | README says user must install |
| Live websocket dependency | Implicit `websocket-client` import | [todo] | Runtime protocol remains unverified; should become an optional extra |
| Cache dependency | `diskcache` remains a package dependency | [doing] | Legacy cache side effects were removed; move it to an optional extra when persistence is implemented |

## OpenAlgo API Compatibility

| Capability | Expected current support | Repo status | Verification status | Notes |
| ---------- | ------------------------ | ----------- | ------------------- | ----- |
| Ping | `/api/v1/ping` | Adapter supports REST call | [done] | Verified 2026-08-31 against local Zerodha-backed target: `{status, data}` with `broker`, `message` |
| Intervals | `/api/v1/intervals` | Adapter supports REST call; store still uses static map | [done] | Verified response is `{status, data}` with minute/hour/day lists; runtime discovery remains pending |
| History | `/api/v1/history` / Python SDK `history()` | REST adapter used by `OAStore` | [done] | Verified `{status, data}` list of 360 OHLCV rows with integer `timestamp` and `oi` |
| Quotes | `/api/v1/quotes` | Adapter supports REST call | [done] | Verified `{status, data}` quote object; not integrated as feed reconciliation |
| MultiQuotes | `/api/v1/multiquotes` | Adapter supports REST call | [done] | Verified `{status, results}` list envelope |
| Funds | `/api/v1/funds` / SDK method | Adapter supports REST call; broker uses raw SDK heuristics | [done] | Verified `{status, data}` with `availablecash`, collateral, and margin fields |
| Orderbook | `/api/v1/orderbook` | Adapter supports REST call; not used for reconciliation | [done] | Verified `{status, data}` with `orders`, `statistics` |
| Tradebook | `/api/v1/tradebook` | Broker uses raw SDK; adapter supports REST call | [done] | Verified `{status, data}` list; broker normalization remains pending |
| Positionbook | `/api/v1/positionbook` | Adapter supports REST call; not used for reconciliation | [done] | Verified `{status, data}` list |
| Analyzer | `/api/v1/analyzer` | Adapter supports REST call; broker does not yet gate routing | [done] | Verified live-capable target reports `mode: live`, `analyze_mode: false`; live routing must remain blocked by future safety guard |
| Websocket auth | `apikey` versus `api_key` naming | Current code sends `api_key` | [blocked] | Need live verification |
| Websocket subscribe | `subscribe` action with symbol/exchange/mode | Implemented | [blocked] | Need live verification |

## Interval Compatibility

The 2026-08-31 local target reported `1m`, `3m`, `5m`, `10m`, `15m`, `30m`, `60m`, `1h`, and `D`. `OAStore` now discovers and caches these capabilities per instance; broker support can still differ from endpoint validation.

| Interval | Meaning | Current static map | Target status | Verification status |
| -------- | ------- | ------------------ | ------------- | ------------------- |
| `1m` | 1 minute | Yes | Keep | [done] |
| `2m` | 2 minutes | No | Use local `1m` aggregation | [done] |
| `3m` | 3 minutes | Yes | Keep | [done] |
| `5m` | 5 minutes | Yes | Keep | [done] |
| `10m` | 10 minutes | Yes | Keep | [done] |
| `15m` | 15 minutes | Yes | Keep | [done] |
| `20m` | 20 minutes | No | Add | [todo] |
| `30m` | 30 minutes | Yes | Keep | [done] |
| `1h` | 1 hour | Yes | Keep | [done] |
| `2h` | 2 hours | No | Add | [todo] |
| `4h` | 4 hours | No | Add | [todo] |
| `D` | Daily | Yes | Keep | [done] |
| `M` | Monthly | No | Add if Backtrader mapping is viable | [todo] |
| `60m` | 60 minutes | No | Consider aliasing to `1h` only after Backtrader semantics are verified | [done] | Advertised by the verified target; no static map yet. |

## Tested Environment Matrix

Fill this table as probes and integration tests are run.

| Date | OpenAlgo server | OpenAlgo Python client | Broker backend | Backtrader | Python | Result | Notes |
| ---- | --------------- | ---------------------- | -------------- | ---------- | ------ | ------ | ----- |
| 2026-08-31 | Local server version not captured | `>=1.0.29` required | Zerodha | Not tested | 3.12 | [done] | Read-only REST contract verified. See `docs/openalgo-api-observations.md`. |

## Broker Lifecycle (Offline)

- `OABroker.start()` initializes optional funds only on the first start and preserves the local cash ledger across same-object restarts. Writer starting balances capture each run; the monitor starts lazily for upstream orders.
- `OABroker.stop()` signals and wakes the monitor, joins it with a bounded timeout, and clears the active polling registry only after the worker exits. It does not cancel upstream orders or mark them cancelled locally.
- If shutdown times out, `stop()` raises `RuntimeError` and retains the worker handle. `start()` refuses to restart while that worker remains alive; Python cannot interrupt an in-flight API request.
- Same-object stop/start resumes retained pending orders automatically without replacement placement. Process restart recovery and a paper/live soak test remain unverified.
- Regression coverage is in `tests/test_broker_lifecycle.py`, including a real `Cerebro` run ending with an upstream order still open. All transports are fake.

## Same-Object Pending Recovery (Offline)

- After a successful stop, `start()` rebuilds polling from retained Submitted, Accepted, or Partial orders and their original upstream IDs. It automatically starts one monitor when needed, without placing replacement orders or cancelling upstream orders.
- Incomplete, conflicting, duplicate, or orphan identity mappings fail startup locally before recovery changes are applied. A timed-out old monitor still blocks restart until it exits.
- Orders, positions, execution quantity/value baselines, and queued notifications are preserved. Funds never reload on same-object restart, with or without pending orders; periodic account snapshots are diagnostic only, not atomic cash/execution reconciliation.
- Terminal status and explicit quantity targets survive a pause. Unknown-quantity settlement requires a fresh quiet window and successful snapshots; downtime and failed polls cannot confirm cleanup.
- Mappings are memory-only. A new broker object or Python process cannot recover old orders, and this does not attach old strategy orders to a new Cerebro run. See `docs/DESIGN.broker-pending-recovery.md`.
- Coverage is in `tests/test_broker_pending_recovery.py`, including Cerebro shutdown/resume, multi-order IDs, delayed fills, repeated resume, and blocked in-flight shutdown. The full offline suite passed 165 tests; ten repeated recovery/settlement/cancellation/lifecycle runs passed with no monitor leaks. Dotenv loading was disabled and outbound connections blocked. No live orders were sent.

## Cash Snapshot Isolation (Offline)

- Funds initialize local strategy cash only on the first start when `use_funds=True`. Same-object restart preserves the ledger even when no orders remain.
- `reconcile_account()` refreshes diagnostics without changing local cash, value, or starting balances. Delayed tradebook fills therefore cannot be counted again through funds rebasing.
- `get_account_cash()` returns last observed available funds; `get_cash_discrepancy()` returns external available funds minus local cash at refresh time. These are not margin-aware balancing values.
- Disabled funds are not requested. Failed envelopes, invalid funds, and responses spanning stop/start do not replace successful diagnostics. Account transport calls occur outside the state lock; validated diagnostics publish together under it.
- The full offline suite passed 211 tests with two existing Backtrader SyntaxWarnings. Ten repeated accounting/lifecycle runs passed with no monitor leaks, dotenv loading disabled, and outbound connections blocked. Coverage is in `tests/test_broker_cash_reconciliation.py`. No live orders were sent.
- External trades, deposits/withdrawals, margin, corporate actions, and fees beyond configured commission remain unsupported. Position/order snapshots now use strict adapter validation; see the section below. See `docs/DESIGN.broker-cash-reconciliation.md`.

## Strict Account Snapshot Validation (Offline)

- Adapter normalizers reject malformed containers/rows, conflicting aliases, non-finite quantities, aggregate overflow, and conflicting duplicate order IDs. Only explicit empty lists clear diagnostic books; exchange identity is never guessed.
- Positions require exchange, symbol, and finite signed quantity. Orders require stable ID, exchange, symbol, finite nonnegative requested quantity, BUY/SELL action, and nonblank status. Unknown statuses and optional metadata remain diagnostic, not execution evidence.
- Invalid evidence preserves all prior successful funds/position/order diagnostics and their refresh timestamp. Valid snapshots never change local cash, positions, active orders, notifications, or routing. Monitor failures back off and recover when usable evidence returns.
- Coverage is in `tests/test_broker_account_snapshots.py`. The full offline suite passed 392 tests with two existing Backtrader SyntaxWarnings; ten repeated 333-test accounting/lifecycle runs passed with no monitor leaks. Dotenv loading was disabled and outbound connections blocked. No live orders were sent.
- Validation does not prove snapshot freshness/completeness or atomic account reconciliation. Position aggregation remains exchange/symbol based, not product based. See `docs/DESIGN.broker-account-snapshots.md`.

## Broker Request Budgets (Offline)

- Each broker has separate rolling-second budgets: `api_read_rate=10` and `api_write_rate=5`, both positive integers. Adapter and raw-client requests share admission; failed transport attempts consume slots, while simulation does not. Excess calls defer immediately without sleeping or queuing.
- Typed, sanitized adapter errors classify HTTP 429 and recognized SDK throttles. Retry-After headers (seconds or HTTP dates), then JSON `retry_after`, select a shared read/write cooldown; invalid or absent hints fall back to 60 seconds.
- Placement and cancellation are never retried automatically. Deferred placement is rejected locally; deferred cancellation preserves pending monitoring. Failed account refreshes preserve prior diagnostics. Monitor order priority rotates between cycles; budgets and cooldowns survive same-object restart.
- Coverage is in `tests/test_adapter_rate_limits.py` and `tests/test_broker_rate_limiting.py`. The full socket-blocked offline suite passed 455 tests with two existing Backtrader SyntaxWarnings. Ten repeated 99-test rate/lifecycle/recovery runs passed with zero monitor leaks; dotenv loading was disabled. No live orders were sent.
- Limits are broker-local, not IP-wide or distributed. Sustained demand delays monitoring; undersized budgets can prevent paired order reads or complete account refreshes. Deployment quotas, ambiguous placement recovery, process persistence and intentional paper/live verification remain separate work. See `docs/DESIGN.broker-rate-limiting.md`.

## Broker Cancellation (Offline)

- `OABroker.cancel()` requests cancellation only for a broker-owned submitted, accepted, or partial order with an upstream ID. Passing a notification clone uses the broker-owned state. Adapter and raw-client request payloads are unchanged.
- A successful acknowledgement, error, exception, or unavailable transport never produces a local `Canceled` notification or clears tracking. Monitoring continues until an upstream terminal status is observed, unless the broker is stopped.
- A confirmed cancellation applies newly observed tradebook fills before cancelling the remainder. Fully executed orders become `Completed`, including when the full quantity was observed before terminal confirmation; duplicate terminal polls do not reapply execution.
- Network calls do not hold the state lock. Reconciliation checks active ownership again after the response and applies accounting and cleanup under the lock, preventing duplicate accounting from overlapping terminal polls or stale updates after shutdown.
- Coverage uses fake transports and real Backtrader orders in `tests/test_broker_cancel_safety.py`, plus deterministic `Cerebro` confirmation/unconfirmed-request cases in `tests/test_broker_cancellation.py`. This is not live contract verification.
- Terminal cancellation now waits for tradebook settlement as described below. Fills first exposed after terminal cleanup, process restart recovery, and a paper/live soak remain unverified.

## Broker Terminal Settlement (Offline)

- Status is read before tradebook; the calls remain non-atomic. `filled_quantity` reported by status is an optional settlement target, not execution accounting. Requested `quantity` and status prices never fabricate fills.
- Terminal orders remain tracked until tradebook covers the highest explicit target and previously accounted quantity. Completion requires the full requested quantity; contradictory targets above that quantity stay pending. Explicit zero can confirm an unfilled cancellation immediately.
- Without an explicit executed quantity, `terminal_settle_seconds` defaults to 10 seconds and requires at least two successful snapshots. Changed tradebook quantities restart the quiet window; regressing snapshots cannot confirm cleanup. Temporary open statuses do not erase terminal evidence.
- Observed executions update cash and position once, before distinct `Canceled`, `Rejected`, or `Expired` notifications. Full execution takes precedence. Failed envelopes, malformed quantities, invalid prices, and malformed tradebooks cannot confirm settlement.
- The quiet window is a heuristic: unknown fills appearing after cleanup can still be missed. Explicit incomplete or contradictory evidence may remain pending indefinitely. Price corrections, truncated tradebooks, process restart, and intentional paper/live verification remain separate work.
- Deterministic coverage is in `tests/test_broker_terminal_settlement.py`. The full offline suite passed 139 tests, with ten repeated settlement/cancellation/lifecycle runs and no monitor thread leaks. No live orders were sent.

## Five-Minute Local Simulation Example (Offline)

- `examples/sma_crossover_5m.py` runs a long-only SMA crossover with deterministic synthetic session bars by default. Both selectable brokers are local-only: ordinary Backtrader next-bar-open execution and OABroker immediate-current-close integration simulation, with zero fees/slippage.
- Optional exported-credential history requests use only native `5m` REST history through OpenAlgoAdapter, `load_env=False`, and an inert SDK client. The historical adapter never reaches the simulated broker. No live historical fetch or order probe was performed for this example.
- Signal/fill timing, cash/value, no-trade and margin cases, invalid input, fake history transport, and final-bar undelivered notifications are covered by 66 real-Cerebro/offline tests in `tests/test_sma_crossover_example.py`. Final order snapshots distinguish alive orders from already-completed fills whose strategy notification was not delivered; trade/drawdown analyzers are not rewritten.
- Full validation passed 521 tests with two existing Backtrader SyntaxWarnings, five repeated example runs, both text/JSON CLI modes, and zero monitor leaks. Outbound socket connections and implicit dotenv loading were blocked. Neither this diagnostic nor passing offline tests establishes production live-trading readiness.

## Known Compatibility Risks

- Websocket authentication key name may have changed or may differ between docs and implementation.
- OpenAlgo endpoint response envelopes are not fully consistent across endpoints.
- The Python SDK may return pandas DataFrames for history while direct REST returns JSON.
- Broker-level interval validation may accept intervals that the underlying broker cannot actually serve.
- Live trading should not be enabled without checking analyzer/paper/live mode first.
- Websocket protocol has not been verified against this target.

## Release Packaging Validation

- Version 0.1.12 explicitly uses `setuptools>=77` with `setuptools.build_meta`; no Hatchling migration is required.
- Wheel and source distribution passed `twine check --strict` and clean Python 3.12.12 installations with dependency consistency checks.
- Adapter/store/feed/history imports passed without optional Backtrader; adding Backtrader 1.9.78.123 also enabled broker imports. Both clean installs resolved OpenAlgo 2.0.5. Imports ran with dotenv loading disabled and outbound sockets blocked.
- These checks establish packaging/import compatibility only, not deployed SDK, websocket, paper-order or live-order compatibility. The development suite passed 521 offline tests with two existing Backtrader SyntaxWarnings.
- Examples remain repository-only and are included with their tests and guide in the reviewed release source. Release commit `002cb7b348e132aa229f1e9e647e2faceb3e84f4` and annotated tag `v0.1.12` are pushed. Both distributions are published on PyPI; downloaded SHA-256 hashes match the validated clean-commit build. See `docs/task-log.md`.

## Next Updates

- [done] Captured sanitized read-only REST observations in `docs/openalgo-api-observations.md`.
- [todo] Add sanitized fixtures from actual responses.
- [todo] Add sanitized fixtures from successful and failure responses, then update the matrix after websocket and broker-lifecycle verification.
