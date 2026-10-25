# Broker cash snapshot isolation

## Problem

OpenAlgo funds, order status, positionbook, orderbook, and tradebook are separate requests. They do not provide a common execution watermark. Replacing local cash with available funds and later applying a delayed fill double counts that fill. Even without pending local orders, a stale funds snapshot can undo a completed fill. Available funds can also reflect margin and reservations rather than the stock-like cash ledger modeled here.

## Decisions

- Load valid funds as the initial local cash baseline only on the first start when `use_funds=True`. Preserve adapter priority and raw-client fallback. Failure retains configured fallback cash. Do not reload cash on same-object restart, including when all local orders have completed.
- Keep local cash execution-ledger driven thereafter. Periodic or explicit `reconcile_account()` never assigns local cash/value or starting balances from funds, even when no orders remain.
- Record available funds separately as `_external_cash`, expose it with `get_account_cash()`, and record `_cash_discrepancy` as external available funds minus local cash at the snapshot publication time. This is a diagnostic difference, not a balancing adjustment or a comparable margin-aware balance.
- Respect `use_funds=False` during reconciliation as well as startup. Position and order snapshots can still be refreshed.
- Fetch account responses without holding the state lock. Validate envelopes at the adapter boundary and reject non-finite/bool funds. Publish the complete diagnostic refresh together under the lock, comparing current local positions/cash. Failed refreshes preserve prior diagnostics and their successful-refresh timestamp.
- Discard an in-flight account refresh after stop or stop/start by checking a lifecycle generation. Do not let stale responses replace diagnostics for a new local run.
- No new routing option, upstream order behavior, automatic correction, deposit/withdrawal adoption, or guessed execution offset is introduced. `setcash()` remains the caller's explicit local adjustment.

## Work plan

- [x] Add deterministic regressions for delayed fills, duplicate snapshots, buy/sell cash, completed-order refresh, restart, disabled funds, invalid funds, failed refresh, transport locking, and lifecycle races.
- [x] Demonstrate failing regressions offline before implementing the policy.
- [x] Isolate funds from the local execution ledger and publish validated diagnostics atomically.
- [x] Run the full offline suite, repeat accounting/lifecycle cases, and inspect monitor leaks with dotenv loading disabled and outbound connections blocked.
- [x] Update compatibility, modernization, and task documentation.

## Remaining limitations

Local cash uses signed execution notional and configured commission, not a broker margin model. External trades, fees, corporate actions, deposits, withdrawals, price corrections, process recovery, truncated tradebooks, and paper/live contracts remain separate work. Position/order snapshots remain diagnostic and are not imported as strategy-owned orders or positions. Row-level position/order normalization retains its existing permissive behavior; a successful envelope is not proof of complete account evidence.
