# Updatation Plan

This document tracks the plan to keep `openalgo-backtrader` current with OpenAlgo and Backtrader expectations.

Status legend:

- `[todo]` Not started
- `[doing]` In progress
- `[done]` Completed
- `[deferred]` Intentionally postponed
- `[blocked]` Waiting on external information or live environment access

## Current Snapshot

- Package version: `0.1.12`
- Main package: `src/openalgo_bt`
- Main components:
  - `stores/oa.py`: OpenAlgo store/client wrapper and Backtrader timeframe mapping
  - `feeds/oa.py`: Backtrader historical/live data feed with local intraday aggregation
  - `brokers/oabroker.py`: Minimal Backtrader broker for OpenAlgo order routing
  - `utils/history.py`: Historical data helper and legacy cache helpers
- No `docs/` folder existed before this plan.
- No checked-in automated test suite was found during the initial scan.
- Current known untracked local files are environment/build artifacts: `.DS_Store`, `.env`, `openalgo_backtrader.egg-info/`, and `src/openalgo_backtrader.egg-info/`.

## Guiding Principles

1. Prefer runtime OpenAlgo capability detection over static assumptions.
2. Centralize OpenAlgo REST and websocket protocol handling in one adapter layer.
3. Keep Backtrader-facing APIs stable where possible.
4. Separate backtest, paper, and live trading modes clearly.
5. Never require a live OpenAlgo server for the unit test suite.
6. Keep live OpenAlgo probes optional and explicit.
7. Document every supported, unsupported, and broker-dependent feature.

## Phase 0: Planning and Baseline Documentation

- [done] Scan repository structure.
- [done] Review README and package metadata.
- [done] Review store, feed, broker, and history helper implementation.
- [done] Draft initial modernization plan in assistant response.
- [done] Add this tracked plan document.
- [done] Add a compact compatibility matrix document.
- [done] Add an implementation task log and keep it updated after each change.

## Phase 1: OpenAlgo Compatibility Audit

Goal: understand what the current OpenAlgo server and Python client actually support.

Tasks:

- [done] Add `scripts/probe_openalgo.py`.
- [todo] Probe `/api/v1/ping`.
- [todo] Probe `/api/v1/intervals`.
- [todo] Probe `/api/v1/history` for daily and intraday candles.
- [todo] Probe `/api/v1/quotes` and `/api/v1/multiquotes`.
- [todo] Probe `/api/v1/funds`, `/api/v1/orderbook`, `/api/v1/tradebook`, and `/api/v1/positionbook`.
- [todo] Probe `/api/v1/analyzer` before any order-routing work.
- [blocked] Probe websocket authentication and subscription shapes. Requires running OpenAlgo websocket endpoint.
- [doing] Save sanitized response examples as test fixtures. Initial representative fixtures added; live sanitized captures still pending.

Deliverables:

- [done] `docs/compatibility.md`
- [todo] `docs/openalgo-api-observations.md`
- [doing] `tests/fixtures/openalgo/*.json`

## Phase 2: OpenAlgo Adapter Layer

Goal: remove scattered assumptions about OpenAlgo response shapes from store, feed, broker, and utils.

Tasks:

- [done] Add `src/openalgo_bt/adapters/openalgo.py` or equivalent.
- [done] Normalize envelopes: `{status, data}`, `{status, results}`, bare arrays, DataFrames, and error payloads.
- [done] Detect HTML responses from missing endpoints and classify them as unsupported, not transient failures.
- [done] Centralize initial API key, host, and client configuration in adapter skeleton.
- [done] Support Python SDK calls plus direct REST fallback for initial read-only endpoints.
- [done] Add typed exceptions for unsupported endpoint, auth failure, validation failure, and transport failure.
- [done] Route `OAStore` historical fetches through the adapter.
- [todo] Route `OAData` through the adapter.
- [todo] Route `OABroker` through the adapter.

Deliverables:

- [done] Adapter module
- [done] Initial unit tests around fixture response normalization and REST fallback

## Phase 3: Interval and Timeframe Refresh

Goal: support current OpenAlgo intervals while preserving broker-dependent fallback behavior.

Tasks:

- [todo] Expand static interval mapping to include `2m`, `20m`, `2h`, `4h`, and `M` where Backtrader can represent them.
- [todo] Add runtime interval discovery through `/api/v1/intervals`.
- [todo] Add a clear fallback policy:
  - Use requested interval directly when supported.
  - Derive minute/hour bars from `1m` when possible.
  - Raise a clear unsupported interval error otherwise.
- [todo] Unit test local aggregation for `1m` to `2m`, `5m`, `15m`, and `1h`.
- [todo] Document that validation support and broker data support may differ.

Deliverables:

- [todo] Updated `OAStore.bt_to_interval()`
- [todo] Interval capability tests
- [todo] README interval section update

## Phase 4: Historical Data Cleanup

Goal: make historical data deterministic, portable, and testable.

Tasks:

- [todo] Remove import-time OpenAlgo client initialization from `utils/history.py`.
- [todo] Remove hardcoded user-specific cache paths.
- [todo] Add configurable cache directory using `OPENALGO_BT_CACHE_DIR`.
- [todo] Make caching optional.
- [done] Normalize all candle rows to `datetime`, `open`, `high`, `low`, `close`, `volume`, `openinterest` in adapter helper.
- [doing] Support pandas DataFrame, REST JSON arrays, epoch seconds, epoch milliseconds, and ISO timestamps.
- [doing] Add tests for empty and malformed responses.

Deliverables:

- [todo] Refactored history helper
- [todo] Historical normalization tests

## Phase 5: Live Feed and Websocket Refresh

Goal: make live data behavior explicit, observable, and compatible with current OpenAlgo.

Tasks:

- [blocked] Verify current websocket authentication payload. Current repo uses `api_key`; known integration notes indicate `apikey` may be required.
- [todo] Add compatibility handling for auth payload naming if needed.
- [todo] Normalize websocket tick parsing in one place.
- [todo] Improve feed states and notifications.
- [todo] Harden shared websocket hub refcounting and reconnect behavior.
- [todo] Add optional periodic REST reconciliation for stale websocket streams.
- [todo] Add fake websocket tests.

Deliverables:

- [todo] Refreshed websocket hub
- [todo] Live feed tests
- [todo] Updated live examples

## Phase 6: Broker and Order Lifecycle

Goal: make order routing safer and closer to Backtrader semantics.

Tasks:

- [todo] Add explicit broker mode: `backtest`, `paper`, `live`.
- [todo] Read OpenAlgo analyzer mode before routing orders.
- [todo] Refuse live order placement when configured mode and OpenAlgo mode do not match.
- [todo] Normalize order payload fields and string/number requirements.
- [todo] Improve support for market, limit, stop, and stop-limit orders.
- [todo] Reconcile order status with tradebook/orderbook.
- [todo] Handle partial fills and weighted average price without double-counting.
- [todo] Document unsupported advanced order types.
- [deferred] Implement GTT/bracket/OCO support. This should follow after base order lifecycle is reliable.

Deliverables:

- [todo] Safer broker implementation
- [todo] Broker lifecycle tests
- [todo] Broker paper/live examples

## Phase 7: Automated Tests and CI Readiness

Goal: make future OpenAlgo changes cheap to evaluate.

Tasks:

- [done] Add `pytest` dev dependency.
- [done] Add initial fixture-based unit tests that do not need a live OpenAlgo server.
- [todo] Add optional integration test markers for live OpenAlgo.
- [done] Add test commands to README.
- [todo] Add linting if desired.

Suggested tests:

- [todo] `tests/test_interval_mapping.py`
- [doing] `tests/test_history_normalization.py` behavior currently covered in `tests/test_openalgo_adapter.py`
- [todo] `tests/test_resampling.py`
- [done] `tests/test_openalgo_adapter.py`
- [todo] `tests/test_feed_live_ws.py`
- [todo] `tests/test_broker_order_mapping.py`
- [todo] `tests/test_broker_lifecycle.py`

## Phase 8: Examples and Documentation

Goal: make correct usage obvious.

Tasks:

- [done] Add `.env.example`.
- [todo] Split examples by use case.
- [todo] Add historical daily example.
- [todo] Add historical intraday example.
- [todo] Add live feed example.
- [todo] Add simulated backtest broker example.
- [todo] Add paper order broker example.
- [todo] Document troubleshooting for auth, unsupported endpoints, missing intervals, and websocket connection issues.

Deliverables:

- [todo] Updated README
- [todo] `docs/troubleshooting.md`
- [todo] Clean examples directory

## Phase 9: Packaging Modernization

Goal: keep package installation flexible and predictable.

Tasks:

- [done] Decide whether to keep Python `>=3.8` or move to a newer baseline. Set to `>=3.12` to match current OpenAlgo dependency resolution.
- [doing] Move optional dependencies into extras:
  - `backtrader`
  - `live`
  - `cache`
  - `dev` [done initial pytest entry]
- [todo] Avoid requiring `diskcache` unless cache support is requested.
- [todo] Add build-system config if needed for consistent builds.
- [todo] Confirm `uv build` works.
- [done] Confirm `uv run --extra dev pytest tests/test_openalgo_adapter.py tests/test_store.py` works after initial tests are added.

Deliverables:

- [doing] Updated `pyproject.toml`
- [doing] Repeatable build/test commands

## Proposed Release Milestones

### `0.2.0`: Compatibility foundation

- [doing] Adapter layer
- [todo] Interval discovery
- [doing] Historical normalization
- [todo] Hardcoded path cleanup
- [doing] Initial tests

### `0.3.0`: Live feed refresh

- [todo] Websocket protocol refresh
- [todo] Shared hub hardening
- [todo] Live feed examples and tests

### `0.4.0`: Broker safety

- [todo] Analyzer mode guard
- [todo] Better order lifecycle
- [todo] Tradebook/orderbook reconciliation

### `1.0.0`: Stable API

- [todo] Documented compatibility matrix
- [todo] Stable public API
- [todo] Sufficient test coverage
- [todo] Explicit limitations

## Running Task Log

### 2026-08-24

- [done] Created `docs/updatation-plan.md`.
- [done] Captured current repo structure and known untracked artifacts.
- [done] Created `docs/compatibility.md`.
- [done] Created `docs/task-log.md`.
- [done] Added `.env.example`.
- [done] Added `scripts/probe_openalgo.py` for read-only live API compatibility checks.
- [done] Added initial OpenAlgo response fixtures under `tests/fixtures/openalgo/`.
- [done] Updated README to point to the planning and tracking docs.
- [done] Prepared lightweight planning/docs scaffolding before deeper code changes.
- [done] Added initial OpenAlgo adapter skeleton and normalization tests.
- [doing] Starting compatibility foundation code changes.
- [done] Added direct REST fallback helpers for read-only OpenAlgo endpoints.
- [done] Added historical candle normalization helper and tests.
- [done] Routed `OAStore.fetch_historical()` through `OpenAlgoAdapter`.
- [done] Raised package Python baseline to `>=3.12` because current OpenAlgo dependency resolution is unsatisfiable for the old `>=3.8` range.
- [done] Verified initial tests with `uv run --extra dev pytest tests/test_openalgo_adapter.py tests/test_store.py`.

## Production Readiness Implementation Plan

This section refines the milestone plan above into the delivery sequence for a complete, efficient, and reliable Backtrader integration. It is the tracking checklist for work that affects live data or order routing.

### Definition of Done

The package is ready for a documented release only when all supported features meet these conditions:

- The OpenAlgo request, response, and websocket contracts are verified against an intentional target and captured in sanitized fixtures.
- Offline tests cover all supported historical-data, resampling, live-feed, and broker-state behavior. Live integration tests are opt-in.
- The public API distinguishes backtest, paper, and live modes, and live execution fails closed unless explicitly enabled and verified.
- A process restart, reconnect, transient request failure, or partial fill cannot silently create an incorrect data bar, order state, position, or balance.
- Unsupported broker features and backend-dependent limitations are rejected or documented explicitly rather than guessed.

### Phase A: Verify the OpenAlgo Contract

Goal: replace protocol assumptions with tested evidence before extending feed or broker behavior.

- [done] Ran `scripts/probe_openalgo.py` against the intentional local Zerodha-backed target. Ping, intervals, history, quotes, multiquotes, funds, orderbook, tradebook, positionbook, holdings, and analyzer succeeded. Sanitized results are in `docs/openalgo-api-observations.md`.
- [todo] Capture and sanitize successful, empty, validation-error, authentication-error, rate-limit, and transport-error response examples.
- [todo] Verify websocket URL, authentication field name, authentication acknowledgement, subscribe/unsubscribe acknowledgement, heartbeat, tick, error, and disconnect payloads.
- [done] Recorded observed REST contracts, verified intervals, broker backend, analyzer state, and environment-precedence caveat in `docs/openalgo-api-observations.md`. Server and SDK versions remain to be captured.
- [done] Updated `docs/compatibility.md` with captured REST endpoint envelopes, observed intervals, and known unverified websocket and broker-lifecycle limitations.

Exit criteria: every endpoint or websocket message used by the library has a documented request and normalized response fixture. Unverified capabilities are disabled or clearly experimental.

### Phase B: Centralize Protocol and Configuration Boundaries

Goal: make one tested adapter responsible for all OpenAlgo-specific behavior.

- [todo] Route `OAData` historical requests through `OpenAlgoAdapter`; remove duplicate REST-envelope assumptions.
- [todo] Route `OABroker` account, order, tradebook, order-status, and reconciliation calls through adapter methods.
- [todo] Add a websocket protocol adapter for auth payloads, subscription messages, acknowledgements, tick normalization, and protocol errors.
- [todo] Standardize websocket configuration on `OPENALGO_WS_URL`; support `WEBSOCKET_URL` only as a documented backwards-compatible alias during migration.
- [done] Refactor or retire `utils/history.py` so importing it does not construct a client or use hardcoded local paths. Implemented in `src/openalgo_bt/utils/history.py`; covered by `tests/test_history_utils.py`.
- [done] Make cache support optional, configurable through `OPENALGO_BT_CACHE_DIR`, and disabled by default unless explicitly requested. `cache_directory()` resolves configuration without creating a cache; persistence remains intentionally disabled until invalidation semantics are implemented.
- [todo] Replace broad silent exception handling at API boundaries with typed exceptions and safe, redacted structured logging.

Exit criteria: feeds, brokers, and utilities do not directly encode OpenAlgo HTTP, SDK, websocket, authentication, or envelope behavior outside the adapter boundary.

### Phase C: Correct Historical Data and Bar Aggregation

Goal: make historical and locally aggregated bars deterministic and testable.

- [done] Implemented per-store runtime interval discovery through `/api/v1/intervals`; `OAStore.available_intervals()` caches the normalized capability set until explicit refresh, covered by `tests/test_store.py`.
- [done] Implemented selection of a server-advertised native interval with a 1-minute local aggregation fallback only when the target advertises `1m`; unsupported combinations raise a clear error. Covered by `tests/test_store.py`.
- [todo] Expand Backtrader timeframe mapping only after server and Backtrader semantics are verified for `2m`, `20m`, `2h`, `4h`, and monthly bars.
- [todo] Add fixture-driven tests for DataFrame, `{data}`, `{results}`, bare-array, empty, malformed, epoch-second, epoch-millisecond, ISO, timezone-aware, and timezone-naive history payloads.
- [doing] Added deterministic historical resampling coverage for 1m to 5m and 1h, including IST bucket alignment and OHLCV aggregation in `tests/test_feed_resampling.py`; add 2m, 15m, and 30m cases next.
- [todo] Test session boundaries, holidays, incomplete buckets, out-of-order candles, duplicates, and missing candles.
- [todo] Specify and test volume behavior for per-tick quantity, cumulative volume, resets, unavailable volume, and negative deltas.

Exit criteria: supported OHLCV output is deterministic for documented inputs, and unsupported interval or timestamp inputs fail clearly rather than silently producing questionable bars.

### Phase D: Harden Live Data

Goal: make live feeds observable, recoverable, and safe to use with strategies.

- [todo] Replace the websocket connection loop with explicit states: disconnected, connecting, authenticated, subscribed, live, stale, reconnecting, and failed.
- [todo] Add bounded exponential reconnect backoff with jitter, error classification, and a configurable retry policy.
- [todo] Confirm subscription state from protocol acknowledgements and resubscribe all active topics after reconnect.
- [todo] Add bounded per-feed queues and a documented overflow policy; expose dropped-bar or dropped-tick conditions.
- [todo] Detect stale streams and notify Backtrader and logs instead of continuing silently.
- [todo] Add optional periodic REST reconciliation for configured stale-stream or gap conditions.
- [todo] Remove duplicate or dead websocket paths, debug `print()` calls, and swallowed errors from `OAData`.
- [todo] Add fake-websocket tests for authentication, subscribe, unsubscribe, reconnect, reconnect resubscription, heartbeat, malformed ticks, duplicates, out-of-order ticks, gaps, and clean shutdown.
- [todo] Add a multi-symbol soak test that validates shared-hub reference counting and does not leak threads after feeds stop.

Exit criteria: a healthy, stale, disconnected, or failed feed is distinguishable to both Backtrader and operators, and reconnect behavior has automated tests.

### Phase E: Implement a Safe Broker Lifecycle

Goal: support a deliberately limited order set correctly before adding advanced order types.

- [done] Added explicit `backtest`, `paper`, and `live` broker modes, defaulting to `backtest`, in `OABroker`.
- [done] Live routing now requires both `allow_live=True` and `OPENALGO_BT_ALLOW_LIVE=1`.
- [done] Non-simulated routing now queries the adapter's analyzer endpoint and rejects paper/live target mismatches before placement.
- [done] Defined the initial supported payload matrix: `MARKET`, `LIMIT`, `SL-M`, and `SL` only. Bracket, OCO, GTT, and other price types are locally rejected by the payload validator until separately verified.
- [doing] `OABroker` routes placement and cancellation through `OpenAlgoAdapter` when present, using a canonical validated market, limit, stop-market, or stop-limit payload. Raw SDK fallback remains for compatibility. Funds, tradebook, and order status are adapter-routed. Full account reconciliation is still pending.
- [todo] Implement an order state machine for created, submitted, accepted, partial, completed, cancelled, rejected, expired, and unknown states.
- [todo] Add idempotency handling where supported, and preserve an ambiguous state rather than re-submitting after uncertain transport failures.
- [doing] Replaced one polling thread per order with a broker-level monitor thread protected by a state lock. The monitor uses wake-up signaling, bounded exponential backoff up to 30 seconds after reconciliation errors, and cleans terminal state. Broker-local request budgets and server cooldowns are implemented as tracked below; full account reconciliation remains outstanding.
- [done] Separated broker startup and shutdown hooks. Startup is idempotent and preserves funds/fallback behavior; shutdown wakes and joins the lazy monitor without cancelling upstream orders. A join timeout retains the monitor handle and blocks restart until that worker exits. Same-object pending-order recovery is now covered separately below.
- [done] Separated cancellation requests from terminal confirmation. Active orders and fill baselines survive successful, failed, or unavailable cancel transports; status polling confirms cancellation after accounting for observed fills. Full executed quantity wins a cancellation race. Offline coverage is in `tests/test_broker_cancel_safety.py` and `tests/test_broker_cancellation.py`; live contracts remain unverified.
- [done] Added delayed terminal settlement with optional explicit status quantity targets and a configurable quiet window when quantities are unknown. Failed/malformed polling evidence does not confirm cleanup; full execution, cancellation, rejection, and expiry remain distinct. Offline coverage is in `tests/test_broker_terminal_settlement.py`; unknown fills after cleanup, process restart recovery, and live verification remain outstanding. See `docs/DESIGN.broker-terminal-settlement.md`.
- [done] Added pure account normalizers and controlled account diagnostic refresh. Funds seed local cash only on first start; later snapshots never overwrite the execution ledger. External cash and its diagnostic difference are separately exposed; failed or stale lifecycle refreshes preserve prior diagnostics. See `docs/DESIGN.broker-cash-reconciliation.md` and `tests/test_broker_cash_reconciliation.py`. Margin-aware reconciliation and process recovery remain outstanding; broker-local request budgets are tracked below.
- [done] Added strict adapter validation for complete position/order snapshots, including required identities, finite quantities, aliases, explicit empty containers, and conflicting duplicate IDs. Malformed evidence preserves the previous complete diagnostic refresh and local trading state. Offline suite: 392 passed; ten repeated 333-test accounting/lifecycle runs passed without monitor leaks. See `docs/DESIGN.broker-account-snapshots.md` and `tests/test_broker_account_snapshots.py`. Snapshot freshness/completeness and product-aware accounting remain separate work.
- [done] Added broker-local rolling read/write request budgets, sanitized adapter throttle errors and shared server cooldowns, fair monitor rotation, and same-object restart retention. Writes are never retried automatically. Offline suite: 455 passed; ten repeated 99-test rate/lifecycle/recovery runs passed without monitor leaks. See `docs/DESIGN.broker-rate-limiting.md`, `tests/test_adapter_rate_limits.py`, and `tests/test_broker_rate_limiting.py`. IP-wide quotas, deployment discovery and ambiguous placement recovery remain outstanding.
- [doing] Implemented incremental aggregate tradebook reconciliation: each poll applies only newly observed filled quantity, emits Backtrader `Partial` until the requested quantity is reached, then emits `Completed`. It caps over-reported quantities and protects against repeated identical snapshots. Tradebook and order-status reads now use the adapter; reconciliation against external account state remains outstanding.
- [done] Explicitly declared memory-only order mappings. Same-object stop/start validates and restores pending monitoring without resubmitting; it preserves fill ledgers and resets quiet-window evidence. See `docs/DESIGN.broker-pending-recovery.md` and `tests/test_broker_pending_recovery.py`. Disk persistence and process restart recovery are not supported.
- [doing] Added cancellation, status normalization, exchange-prefix payload helper, validated order-payload, adapter-routing, centralized-monitor, backoff, tradebook-deduplication, and incremental partial-fill tests. Full account reconciliation remains outstanding.

Exit criteria: live routing fails closed, broker state is reconciled to OpenAlgo, and all supported order transitions are covered by deterministic tests.

### Phase F: Quality, Efficiency, and Release Operations

Goal: make ongoing maintenance and diagnosis safe and inexpensive.

- [todo] Add pytest markers for live integration tests and exclude them by default.
- [todo] Add CI for dependency sync, offline tests, linting, static analysis, package build, and install smoke tests.
- [todo] Add coverage reporting and establish a threshold focused on adapter, feed, broker, and state-transition code.
- [todo] Move Backtrader, websocket, cache, and development dependencies to documented optional extras as appropriate.
- [todo] Verify `uv build` and install the built wheel in a clean environment.
- [todo] Add structured, redacted logs and metrics for REST latency, reconnects, subscriptions, queue depth, stale feeds, order transitions, reconciliation differences, and API errors.
- [todo] Document operational runbooks for invalid credentials, endpoint incompatibility, data gaps, websocket outage, stuck order state, reconciliation mismatch, and safe shutdown.
- [todo] Run an opt-in paper-trading soak test covering multiple symbols, reconnects, cancellation, fills, partial fills when available, and process restart recovery.

Exit criteria: CI proves offline correctness, operators can diagnose failures without secrets, and a documented paper-trading soak validates the release candidate.

### Suggested Release Gates

| Milestone | Scope | Release gate |
| --- | --- | --- |
| `0.2.0` | Contract and historical foundation | Verified REST fixtures, adapter consolidation, portable history helper, interval/resampling tests. |
| `0.3.0` | Live feed refresh | Verified websocket contract, stateful hub, fake-websocket suite, stale-feed behavior, multi-symbol soak test. |
| `0.4.0` | Broker safety | Explicit modes, analyzer guard, centralized monitor, reconciliation, lifecycle test suite, paper-only soak test. |
| `1.0.0` | Stable supported API | CI and packaging gates, documented compatibility matrix, explicit limitations, and successful release-candidate paper soak. |

### Progress Update Protocol

For each completed checklist item:

1. Change its marker to `[done]` only after its tests or live evidence are recorded.
2. Link the relevant test file, sanitized fixture, probe output summary, or documentation section in `docs/task-log.md`.
3. Update `docs/compatibility.md` when observed OpenAlgo behavior changes the supported contract.
4. If an external capability is unavailable, use `[blocked]`, state the dependency and safe fallback, and do not claim support.
