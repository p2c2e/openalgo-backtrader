# Strict broker account snapshots

## Problem

Before this change, account reconciliation skipped malformed rows, treated unrecognized containers as empty books, guessed NSE for missing exchanges, accepted non-finite position quantities, and overwrote duplicate order IDs. A successful envelope was not proof of usable account evidence. Publishing those results cleared earlier diagnostics and advanced their successful-refresh timestamp.

## Decisions

- Put endpoint envelope, container, and row validation in `src/openalgo_bt/adapters/openalgo.py`. Keep existing `positions_by_symbol()` and `orders_by_id()` entry points as adapter-backed projections, not permissive parsers.
- Accept raw lists or explicit `positions`/`orders` list containers after account-envelope normalization. Preserve `data`, `results`, text JSON, and `NormalizedOpenAlgoResponse` support. Only an explicit empty list is an empty book; missing/null/wrong-type containers fail with `OpenAlgoResponseError`.
- Require every position row to have a nonblank string exchange, nonblank symbol (`symbol` or `tradingsymbol`), and finite signed quantity (`quantity`, `net_quantity`, or `netqty`). Never guess an exchange. Boolean quantities and aggregate overflow fail. Multiple valid product rows for the same exchange/symbol are still aggregated, including zero and short quantities.
- Require every order row to have a stable nonblank ID (`orderid`, `order_id`, or `id`), exchange, symbol, finite nonnegative requested quantity, BUY/SELL action, and nonblank `order_status` or `status`. Retain unknown status strings as diagnostic metadata, not invented lifecycle evidence. Preserve legacy nonnegative integer IDs by converting them to strings; reject bool, float, container, and null IDs.
- Present aliases must agree after their field-specific normalization; a valid alias cannot hide an invalid or conflicting one. Keep exact text identity (no silent trimming beyond a blank check). Normalize action case for validation only. Unused product/price/timestamp/other metadata is preserved, not interpreted as execution evidence.
- Identical duplicate order rows may collapse to one ID. Conflicting rows for an ID reject the entire refresh rather than selecting a winner. No position deduplication is guessed because product rows can legitimately share a symbol.
- Reject the entire snapshot if any row is malformed. Errors identify the book, row index, and field without echoing account values or complete payloads.
- Preserve existing all-or-nothing broker diagnostic publication: either funds, positions, orders, discrepancies, and timestamp refresh together, or prior successful diagnostics remain untouched. Local cash/value, positions, active orders, notifications, and routing never change as a result of account snapshots. Failed monitor refreshes continue through existing backoff handling.
- This deliberately tightens undocumented permissive input behavior. Existing ID-only order fixtures must become representative complete diagnostic rows. No new live call, routing default, version bump, persistence, or automatic account import is introduced.

## Protocol evidence

Context7 `/marketcalls/openalgo-docs`, accounts API and Python examples, describes positionbook `data` as a list with string exchange/symbol and signed string quantity. Orderbook `data` contains `orders` and optional `statistics`; rows include orderid, symbol, exchange, action, quantity, and order_status. This documentation guides fixture construction, not a claim of live verification. Existing local REST observations confirm envelope/container shapes only.

## Work plan

- [x] Add deterministic adapter/projection and broker regressions for malformed containers/rows, conflicting aliases/IDs, numeric edge cases, valid empty books, documented rows, failed refresh preservation, and recovery.
- [x] Demonstrate regression failures against the current implementation offline.
- [x] Implement strict adapter validation and adapter-backed account projections.
- [x] Run the full offline suite with dotenv loading disabled and outbound connections blocked; repeat accounting/lifecycle cases and assert no monitor leaks.
- [x] Update compatibility and modernization/task documentation with verified behavior and limitations.

## Verification

The pre-change regression baseline failed 173 cases and passed 8. After implementation, the full socket-blocked offline suite passed 392 tests with two existing Backtrader SyntaxWarnings. Ten repeated 333-test accounting/lifecycle runs passed without monitor leaks; `git diff --check` passed. Dotenv loading was disabled, no live orders were sent, and release-preparation version remains 0.1.12.

## Remaining limitations

A valid snapshot can still be stale, truncated, non-atomic, or semantically incomplete. Position aggregation is by exchange/symbol, not product; quantities do not establish ownership. Optional metadata is not certified. Price corrections, external account changes, margin-aware accounting, process recovery, API rate limiting, and deliberate paper/live contracts remain separate work.
