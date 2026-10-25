# Broker Request Budgets

## Goal

Bound bursts from the centralized monitor and synchronous broker calls without
blocking shutdown on a sleep or automatically retrying a non-idempotent write.
This is an offline safety improvement, not live-contract verification.

## Decisions

- One thread-safe limiter per OABroker covers adapter and raw-client calls.
  Analyzer, funds, positionbook, orderbook, orderstatus and tradebook share a
  rolling read budget. Placement and cancellation share a separate write budget.
- Public parameters `api_read_rate=10` and `api_write_rate=5` are positive integer
  request budgets per rolling second. Failed transport attempts consume budget.
  Simulation consumes no budget. Excess calls fail immediately with a typed
  local deferral, not a sleep, queue or automatic retry.
- Defaults are deliberately below documented OpenAlgo general/order limits
  (50/10 per second, with some builds documenting general limits of 10).
  Configure budgets for the intended deployment. This limiter is per broker,
  not a claim to coordinate separate brokers, adapters, processes or a shared IP.
- A typed adapter error classifies HTTP 429 and recognized SDK rate-limit
  envelopes using a sanitized message. Retry-After seconds or HTTP dates take
  precedence over JSON `retry_after`; absent/invalid hints use a conservative
  fallback. A server throttle applies one cooldown to both broker budgets.
- Never retry placement or cancellation automatically. Analyzer deferral rejects
  a new order before placement. Placement deferral follows existing rejection
  semantics; cancellation deferral retains the pending order until status polling
  confirms its terminal state. Ambiguous placement transport failure handling is
  still a separate readiness item.
- Keep request timestamps and cooldowns across same-object stop/start. Rotation
  of active orders between monitor cycles prevents a permanently privileged
  prefix from using all available read slots. Account diagnostics retain their
  prior successful snapshot when any request is deferred.
- No limiter lock or broker state lock is held during transport. There is no
  background request queue. An already admitted/in-flight request cannot be
  interrupted or recalled by this change.

## Evidence

Context7 OpenAlgo docs: `api-documentation/v1/rate-limiting.md` and
`developers/design-documentation/36-rate-limiting-guide.md`. Limits use client IP
keying and deployment configuration. Documented 429 responses can contain
`retry_after: 60`. Exact deployed endpoint scopes and server versions remain
unverified; do not infer a global cross-endpoint server quota from these docs.

## Plan

- [x] Add deterministic offline limiter, adapter-429 and broker integration tests.
- [x] Implement thread-safe monotonic rolling admission and shared cooldown.
- [x] Route all broker requests through admission without changing payloads.
- [x] Rotate monitor order priority and preserve budgets across restart.
- [x] Run the socket-blocked offline suite and repeated lifecycle/rate regressions.
- [x] Update compatibility and task tracking with limits and evidence.

## Limitations

No distributed/IP-wide quota, deployment limit discovery, automatic order retry,
write idempotency, scheduling guarantees, durable restart recovery, or live soak.
Sustained demand above the read budget delays monitoring; very small budgets can
prevent a status/tradebook pair or a complete account refresh from succeeding.
