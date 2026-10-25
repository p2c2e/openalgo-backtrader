# Task Log

This file tracks intermediate work while updating `openalgo-backtrader`.

Status legend:

- `[todo]` Not started
- `[doing]` In progress
- `[done]` Completed
- `[deferred]` Intentionally postponed
- `[blocked]` Waiting on external information or live environment access

## 2026-08-24

### Planning scaffold

- [done] Scanned repository structure.
- [done] Confirmed no existing `docs/` folder.
- [done] Read `README.md`.
- [done] Read `pyproject.toml`.
- [done] Read `src/openalgo_bt/stores/oa.py`.
- [done] Read `src/openalgo_bt/feeds/oa.py`.
- [done] Read `src/openalgo_bt/brokers/oabroker.py`.
- [done] Read `src/openalgo_bt/utils/history.py`.
- [done] Created `docs/updatation-plan.md`.
- [done] Created `docs/compatibility.md`.
- [done] Created `docs/task-log.md`.

### Findings

- [done] `utils/history.py` initializes an OpenAlgo client at import time.
- [done] `utils/history.py` contains hardcoded cache paths under another local workspace.
- [done] `OAStore.bt_to_interval()` does not cover all currently documented OpenAlgo intervals.
- [done] `OAData` has websocket support, but websocket auth naming needs live verification.
- [done] `OABroker` is a minimal broker and needs analyzer-mode safety before live order routing.

### Planning/doc updates completed


- [done] Added `.env.example`.
- [done] Added `scripts/probe_openalgo.py` for read-only live compatibility checks.
- [done] Added initial `tests/fixtures/openalgo/` response fixtures.
- [done] Updated README to point to planning docs.

### Adapter/test scaffold


- [done] Added initial unit test scaffolding with `tests/test_openalgo_adapter.py`.
- [done] Added OpenAlgo adapter module skeleton in `src/openalgo_bt/adapters/openalgo.py`.
- [done] Added response normalization tests using current fixtures.
- [done] Added initial `pytest` dev extra in `pyproject.toml`.

### Compatibility foundation

- [done] Added direct REST fallback methods to `OpenAlgoAdapter` for initial read-only endpoints.
- [done] Added `normalize_history_candles()` to normalize OpenAlgo history payloads.
- [done] Added tests for direct REST fallback and historical candle normalization.
- [done] Routed `OAStore.fetch_historical()` through `OpenAlgoAdapter`.
- [done] Added `tests/test_store.py` for store-to-adapter routing.
- [done] Set package Python baseline to `>=3.12` after `uv` showed current OpenAlgo dependencies cannot resolve for `>=3.8`.
- [done] Verified `uv run --extra dev pytest tests/test_openalgo_adapter.py tests/test_store.py` passes.

### Current next tasks

- [todo] Split historical normalization tests into a dedicated file if they grow further.
- [todo] Add runtime interval discovery and interval fallback policy.
- [todo] Remove or isolate legacy hardcoded cache helpers from `utils/history.py`.
- [todo] Update `docs/openalgo-api-observations.md` after live probe output is available.


## 2026-08-31

### Historical helper boundary

- [done] Replaced import-time OpenAlgo client creation in `utils/history.py` with explicit `OAStore` and `OpenAlgoAdapter` injection.
- [done] Removed hardcoded cache directories and implicit `diskcache` side effects from legacy helpers.
- [done] Added `cache_directory()` with `OPENALGO_BT_CACHE_DIR` support; caching remains intentionally disabled until invalidation semantics are defined.
- [done] Added offline coverage in `tests/test_history_utils.py` for store delegation, portable cache configuration, date normalization, and per-symbol bulk failures.
- [done] Verified the offline suite with `uv run pytest -q` (15 passed).

### Local REST probe

- [done] Ran the read-only probe with the updated project `.env` while clearing stale inherited `OPENALGO_API_KEY` and `OPENALGO_API_HOST` values. Ping, intervals, history, quotes, multiquotes, funds, orderbook, tradebook, positionbook, holdings, and analyzer all returned HTTP 200 success envelopes.
- [done] Captured sanitized endpoint shapes, observed intervals, the Zerodha backend, and analyzer state in `docs/openalgo-api-observations.md`.
- [done] Updated `docs/compatibility.md` only from the captured REST evidence.
- [blocked] Websocket and order-lifecycle verification remain separate: the target reports `mode: live`, so no order probe will run until explicit paper-safe routing and broker-mode guards exist.


### Interval discovery and aggregation

- [done] Added per-store interval discovery and explicit capability caching through `OAStore.available_intervals()`.
- [done] Added native-interval selection with a verified 1-minute aggregation fallback and clear unsupported-interval failures in `OAStore.resolve_interval()`.
- [done] Updated `OAData` to prefer discovered capabilities before selecting local resampling.
- [done] Added deterministic 5-minute and hourly resampling tests covering IST bucket alignment and OHLCV aggregation in `tests/test_feed_resampling.py`.
- [done] Verified the expanded offline suite with `uv run pytest -q` (21 passed).


### Backtrader contract baseline and routing safety

- [done] Added Backtrader `1.9.78.123` to the development dependency group and verified the actual `TimeFrame` API. This version has no `TimeFrame.Hours`; hourly bars are represented as `TimeFrame.Minutes` with compression `60`. Updated interval mapping and tests accordingly.
- [done] Added a fail-closed routing policy in `src/openalgo_bt/brokers/routing.py`: backtest never routes upstream, paper requires an analyzer-safe target, and live requires both explicit local opt-ins and a matching remote analyzer state.
- [done] Integrated the policy check in `OABroker.submit()` only after the existing simulated-fill path, preserving local backtest execution.
- [done] Added mode-policy tests and a real `Cerebro` simulated-market-order lifecycle test. It verifies `Submitted`, `Accepted`, `Completed`, trade opening, position size, and cash update behavior.
- [todo] Add reference-broker comparison tests for cancellation, rejected routing, partial fills, notification boundaries, and shutdown while orders are active.
- [done] Selected `/Users/sudharsanr/workspace/openalgo_strategies/bt/simple_candle_strategy.py` as a realistic later integration candidate. It is not yet suitable as a baseline fixture because it imports workspace-specific commissions, analyzers, logging, and OpenAlgo history helpers. The repository test baseline remains the smaller deterministic `Cerebro` strategy in `tests/test_backtrader_lifecycle.py`.
- [done] Added `tests/test_time_based_strategy_integration.py`, a dependency-free adaptation of the recommended `backtrader_test2.py` TimeBasedStrategy. It verifies the deterministic 09:30 IST entry, 15:20 IST exit, round-trip order statuses, flat final position, and realized cash gain through `OABroker(simulate_fills=True)`.
- [done] Corrected `OABroker` notification order so `Accepted` is emitted only for a simulated fill or after an upstream order identifier is confirmed. The new rejection test verifies a non-simulated backtest request produces `Submitted`, then `Rejected`, without a position or cash change.
- [done] Added a paper-route cancellation lifecycle test. It verifies the `Submitted`, `Accepted`, `Canceled` ordering, upstream `cancelorder(order_id=...)` call, and cleanup of polling state.
- [done] Aligned the notification dequeue behavior with Backtrader's reference broker, removing the custom `None` sentinel from `OABroker.next()`.
- [done] Added unit coverage for status parsing and exchange-prefix handling. A feed named `NFO:SYMBOL` now routes to NFO unless an explicit exchange override is supplied.
- [todo] Partial executions are still not modeled. Do not enable a paper order probe until partial-fill, duplicate-trade, and reconciliation semantics are implemented.
- [done] Added `brokers/reconciliation.py` and fixture-driven tests to deduplicate stable OpenAlgo execution IDs and calculate aggregate filled quantity plus weighted average price.
- [done] Routed `patched_order_status()` through that helper and added an integration test for duplicate tradebook rows.
- [todo] The poller still treats a terminal order as one full fill. Incremental Backtrader `Partial` execution and reconciliation of later tradebook changes remain required before a paper probe.
- [done] Implemented incremental aggregate fill application in `OABroker`: new tradebook quantity becomes a Backtrader `Partial`; only the observed delta changes cash and position; the requested quantity caps completion; repeated snapshots do not execute again.
- [done] Added partial-fill tests for first partial execution, unchanged duplicate snapshots, completion on later quantity, aggregate-price-to-delta-price conversion, and over-reported fill caps.
- [todo] Partial fill behavior is covered with fake order objects. Add a full `Cerebro` partial-fill lifecycle test and account reconciliation before an approved paper order probe.
- [done] Added adapter `orderstatus()` support and routed broker tradebook/order-status reads through the adapter when available, with raw SDK fallback retained for compatibility.
- [done] Added adapter-boundary tests for the normalized status flow.
- [todo] OpenAlgo placement, cancellation, funds, orderbook, and positionbook calls still bypass the adapter; centralize them before enabling any controlled paper probe.
- [done] Added adapter order-placement and cancellation method surfaces, plus account normalization helpers for verified `availablecash` and positionbook schemas.
- [done] Broker startup now gets funds through `OpenAlgoAdapter` when present, with the raw SDK path retained as a compatibility fallback.
- [todo] Adapter method existence is not broker routing. Placement and cancellation need a canonical payload mapper and controlled integration tests before the broker is switched to REST routing.
- [done] Added a canonical order payload validator and moved broker placement/cancellation onto adapter methods when an adapter is available. The allowed price types are market, limit, stop-market, and stop-limit only.
- [done] Added adapter routing tests for validated placement and cancellation plus payload validation tests for required price and trigger fields.
- [todo] Adapter routing is tested only with fake responses. Before a paper probe, capture the exact local server request/response contract for placeorder and cancelorder without risking account state.
- [done] Replaced the per-order polling-thread design with one broker-level monitor that tracks active upstream order IDs, reconciles each order, and cleans terminal state.
- [done] Added monitor unit tests for terminal cleanup and active orders with no new fill.
- [todo] The monitor currently has a fixed two-second cycle and no synchronization/backoff/rate-control policy. Add those before a paper probe.
- [done] Added broker monitor state locking and wake-up signaling. New orders wake the monitor immediately; shutdown wakes and joins it cleanly.
- [done] Added bounded exponential monitor backoff after reconciliation errors, capped at 30 seconds.
- [done] Re-ran the complete suite after fixing an initial monitor insertion regression: 56 tests pass.
- [done] Added `orders_by_id()` account normalization and `reconcile_account()` for adapter funds and positionbook refresh.
- [done] Integrated controlled account refresh into the centralized monitor with a 30-second cadence.
- [done] Added account-reconciliation tests; full suite now passes with 57 tests.
- [todo] External account snapshots are stored for comparison but are not yet reconciled against every Backtrader-local position/order state. Do not treat this as production-safe account recovery yet.
- [done] Added position discrepancy detection between Backtrader-local positions and external positionbook quantities.
- [done] Added external orderbook snapshot storage during account reconciliation.
- [done] Added tests for missing/mismatched positions and verified the complete suite now passes with 58 tests.
- [todo] Restart recovery and API rate limiting remain before declaring broker implementation complete.

## 2026-10-24

### Standalone broker startup/shutdown fix

- [done] Reproduced the lifecycle regression with eight failing offline tests: `start()` contained shutdown code, left `_started=False`, and called the parent stop hook; inherited `stop()` left the monitor alive.
- [done] Split initialization from shutdown, made repeated lifecycle calls safe, and retained adapter/raw-client funds fallback behavior. Monitoring remains lazy and is not started for simulated backtests.
- [done] Serialized lifecycle operations and monitor creation. Shutdown wakes and joins outside the state lock; a worker that outlasts the timeout keeps its handle and prevents restart until it exits.
- [done] Added fake-transport coverage for funds/fallbacks, repeated calls, clean monitor restart, shutdown timeout, stop checks between orders, and `Cerebro` shutdown while an upstream order is still open.
- [done] Verified the complete offline suite: 68 passed (two existing Backtrader SyntaxWarnings). No broker monitor threads remained after the suite; ten additional lifecycle test runs passed. Tests ran with dotenv loading disabled and no live OpenAlgo target.
- [todo] Pending-order recovery after stop/start and process restart remain separate work. Shutdown never cancels upstream orders or fabricates cancellation notifications.

### Standalone broker cancellation safety fix

- [done] Reproduced 26 failures in offline cancellation regressions: cancel requests cleared polling and fill baselines, fabricated local cancellations, and allowed repeated terminal execution accounting.
- [done] Cancellation now checks broker-owned state, preserves tracking on acknowledgements/errors/exceptions/unavailable transport, and wakes reconciliation without holding the state lock during transport calls.
- [done] Reconciliation applies observed fill deltas before terminal cancellation, prioritizes full execution, promotes previously observed full fills without another execution, and ignores stale terminal responses after cleanup or shutdown.
- [done] Added real-order regressions in `tests/test_broker_cancel_safety.py` and updated `tests/test_broker_cancellation.py` to drive fake confirmation synchronously through `Cerebro`, covering both confirmed and unconfirmed requests. Updated adapter-routing and monitor fixtures to match broker-owned state and locks.
- [done] Final offline suite passed 102 tests (two existing Backtrader SyntaxWarnings). Ten repeated cancellation/lifecycle runs passed with no broker monitor thread leaks. Outbound socket connections were blocked, all transports were fake, and dotenv loading was disabled.
- [todo] Verify intentional paper-target cancellation contracts and delayed tradebook visibility. No live orders were sent; late fills first exposed after terminal cleanup and pending-order restart recovery remain separate work.


### Delayed terminal tradebook settlement

- [done] Added status-before-tradebook polling and adapter-boundary helpers for failed envelopes and optional explicit executed quantity. Requested quantity and status prices are not execution evidence.
- [done] Retained terminal tracking until explicit targets and existing fill baselines are covered; unknown quantities use at least two successful snapshots and the configurable quiet window. New or regressing quantities reset that window.
- [done] Preserved pending terminal evidence across transient open responses, promoted full fills to completion, and emitted distinct cancellation/rejection/expiry after applying observed fills. Invalid quantities/prices and malformed tradebooks fail closed.
- [done] Added deterministic real-order regressions for delayed fills, adapter/raw fallback, malformed evidence, errors, quantity regression, increasing targets, quiet-window configuration, notification ordering, and idempotent accounting.
- [done] Verified 139 offline tests (two existing Backtrader SyntaxWarnings), ten repeated settlement/cancellation/lifecycle runs, and no monitor thread leaks. Outbound connections were blocked and dotenv loading was disabled.
- [done] Bumped release-preparation metadata once from 0.1.11 to 0.1.12 in `pyproject.toml` and the root package entry in `uv.lock`. Nothing was published.
- [todo] Unknown fills first visible after the quiet window, old execution price corrections, truncated tradebooks, restart recovery, API rate limiting, and intentional paper/live contracts remain unverified. No live orders were sent.

### Same-object pending-order recovery

- [done] Confirmed 16 failing regression cases against the prior implementation: retained upstream orders stopped being monitored after local stop/start, and downtime could retain stale terminal evidence.
- [done] Added all-or-nothing retained identity validation and automatic monitor restoration without upstream placement/cancellation. A timed-out worker must exit before recovery can proceed.
- [done] Preserved fill/value baselines, positions, cash, and queued notifications; skipped startup funds reload with pending orders. Reset quiet-window snapshot evidence while retaining terminal status and explicit quantity targets.
- [done] Added 26 offline recovery cases using real orders and fake transports, including Cerebro shutdown/resume, duplicate tradebook accounting, multiple IDs, invalid mapping recovery, repeated resume, and an in-flight response discarded after shutdown.
- [done] Verified 165 offline tests (two existing Backtrader SyntaxWarnings), ten repeated recovery/settlement/cancellation/lifecycle runs, and no monitor leaks. Outbound connections were blocked and dotenv loading was disabled. No live orders were sent.
- [done] Documented memory-only mappings and unsupported new-object/process recovery in `docs/DESIGN.broker-pending-recovery.md` and `docs/compatibility.md`. Release-preparation version remains 0.1.12; nothing was published.
- [todo] Atomic account cash/execution reconciliation, disk persistence/process restart recovery, API rate limiting, and intentional paper/live contract verification remain separate work.

### Cash snapshot isolation

- [done] Reproduced 31 failing offline cash-policy regressions: funds refreshes rebased the local ledger and allowed delayed fills or stale snapshots to corrupt cash.
- [done] Restricted funds initialization to the first start. Periodic/explicit account refreshes and same-object restart preserve local cash/value and execution baselines, including after orders complete.
- [done] Added separately exposed external cash and cash-discrepancy diagnostics, validated account envelopes/funds, honored disabled funds, and published diagnostics under the state lock after unlocked transport calls. Failed and stale lifecycle refreshes preserve prior successful diagnostics.
- [done] Added deterministic delayed-fill, duplicate-snapshot, buy/sell, startup/restart, invalid-response, disabled-funds, lock, and lifecycle-race regressions in `tests/test_broker_cash_reconciliation.py`.
- [done] Verified 211 offline tests (two existing Backtrader SyntaxWarnings), ten repeated accounting/lifecycle runs, and no monitor leaks. Dotenv loading was disabled and outbound connections blocked. No live orders were sent. Version remains 0.1.12; nothing was published.
- [todo] Margin-aware account reconciliation, external balance changes/trades, process persistence, rate limiting, and deliberate paper/live contract verification remain separate work.

### Strict account position/order snapshots

- [done] Reproduced 173 failing offline snapshot regressions against permissive normalization (8 baseline cases passed).
- [done] Added strict adapter container/row validation and adapter-backed projections. Invalid identities, quantities, aliases, and conflicting duplicate IDs reject the whole refresh without guessing an exchange or an empty book.
- [done] Preserved all-or-nothing diagnostic publication and the local trading ledger. Sanitized validation errors identify book/row/field without echoing account values; monitor failures retain diagnostics, back off to the existing cap, and recover on valid evidence.
- [done] Added deterministic coverage in `tests/test_broker_account_snapshots.py` and updated legacy ID-only order fixtures to complete representative rows.
- [done] Verified 392 offline tests (two existing Backtrader SyntaxWarnings), ten repeated 333-test accounting/lifecycle runs, no monitor leaks, and a clean `git diff --check`. Dotenv loading was disabled and outbound connections blocked. No live orders were sent. Version remains 0.1.12; nothing was published.
- [todo] Snapshot freshness/completeness, product-aware accounting, margin/external changes, process recovery, API rate limiting, and deliberate paper/live contracts remain separate work.

### Centralized broker request budgets

- [done] Added monotonic, thread-safe rolling read/write admission for adapter and raw-client broker calls. Default budgets are 10 reads and 5 writes per second; local deferral never sleeps, queues, or retries writes.
- [done] Added sanitized HTTP 429/SDK throttle classification, bounded Retry-After parsing, and a shared server cooldown retained across same-object restart. Analyzer/funds/account/order polling and placement/cancellation all use admission without changing payloads or routing defaults.
- [done] Rotated monitored order priority between cycles and retained pending cancellation state and prior account diagnostics on deferral. Limiter and state locks are not held during transport.
- [done] Added deterministic concurrency, rolling-window, cooldown/header, adapter/raw-client, fairness, restart and shutdown coverage in `tests/test_adapter_rate_limits.py` and `tests/test_broker_rate_limiting.py`.
- [done] Verified 455 offline tests (two existing Backtrader SyntaxWarnings), ten repeated 99-test rate/lifecycle/recovery runs, and zero broker monitor leaks. Outbound socket connections were blocked and dotenv loading disabled. No live orders were sent. Version remains 0.1.12; nothing was published.
- [todo] IP-wide/distributed quotas, deployed quota discovery, write idempotency/ambiguous placement recovery, process restart persistence and deliberate paper/live contracts remain separate work. Very small read budgets can prevent paired order reads or complete account refreshes. See `docs/DESIGN.broker-rate-limiting.md`.
