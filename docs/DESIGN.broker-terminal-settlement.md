# Broker Terminal Settlement

## Purpose

Keep terminal orders tracked while their tradebook evidence catches up. This is an offline hardening step, not a verified live trading contract or restart recovery.

## Evidence and constraints

- Cancellation requests are already separate from terminal confirmation.
- Before this change, the broker read tradebook before order status. A newer terminal status could therefore be paired with an older tradebook snapshot.
- OpenAlgo's documented order-status examples contain requested `quantity`, `average_price`, and `order_status`, but do not consistently expose executed quantity. Requested quantity must never be treated as an executed quantity for cancellation.
- Source: https://github.com/marketcalls/openalgo-docs/blob/main/trading-platform/python/README.md (queried through Context7).
- Preserve placement/cancellation payloads and live-routing guards. Unit tests must remain offline.

## Design

1. Read status before tradebook, so tradebook has a chance to reflect terminal state. This does not make two API calls atomic.
2. Normalize an optional `filled_quantity` from status through a pure adapter helper. Preserve it separately as `reported_filled_quantity`; tradebook quantity and weighted average remain the only execution accounting source. Missing fields are unknown, not zero. Reject malformed, negative, boolean, or non-finite values.
3. Track each pending terminal observation under the broker state lock. Preserve the terminal status, highest explicit executed-quantity target, latest observed tradebook quantity, and time since the last new fill. Temporary open/unknown statuses must not erase the pending terminal evidence.
4. Full tradebook execution completes the order immediately when terminal evidence exists, without duplicate accounting.
5. If an explicit target exists, retain tracking until tradebook covers that target. Never settle incomplete evidence just because a timer elapsed. Conflicting targets above requested quantity remain pending rather than being hidden by accounting caps.
6. Without an explicit target, wait for at least two successful snapshots and a quiet period configured by `terminal_settle_seconds` (default 10 seconds). New fills restart the quiet period. Errors do not create successful snapshots. This is a bounded heuristic, not proof that no later fills exist.
7. Account observed fills as `Partial` before emitting `Canceled`, `Rejected`, or `Expired`. Preserve these distinct terminal notifications. Cleanup removes settlement state alongside existing registries. No additional worker is introduced.
8. Unknown or malformed tradebook payloads are not empty successful snapshots. Invalid/missing execution prices must not fabricate fills at requested prices during settlement.

## Limits

- Unknown quantities first visible after the quiet window remain a documented risk. No tombstone, post-terminal execution correction, or unbounded terminal-history cache is added.
- Explicit targets may leave an order pending indefinitely during an outage or contradictory upstream evidence. Stop remains bounded and does not cancel upstream orders.
- Process restart and stop/start pending-order recovery remain separate work.
- Broker-specific live contracts, aliases other than `filled_quantity`, corrections to old execution prices, and tradebook truncation remain unverified.

## Plan

- [x] Inspect current cancellation, polling, and fixture behavior.
- [x] Check documented order-status response shape through Context7.
- [x] Add deterministic terminal-settlement regressions and confirm baseline failures.
- [x] Implement explicit quantity normalization and pending terminal settling.
- [x] Update existing fake transports for explicit zero/partial execution evidence.
- [x] Run full offline suite and repeated lifecycle/settlement checks with outbound connections blocked.
- [x] Update compatibility, modernization plan, and task log with results and remaining limits.
