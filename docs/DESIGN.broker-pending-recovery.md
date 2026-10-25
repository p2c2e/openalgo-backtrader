# Same-process broker pending-order recovery

## Scope

Resume polling after `stop()` and `start()` on the same `OABroker` object. Preserve the original Backtrader orders, data objects, upstream IDs, execution ledgers, positions, cash, and notification queue. This is not disk persistence, process restart recovery, or a promise that a new Cerebro run adopts old strategy orders.

## Decisions

- Shutdown still stops only local monitoring. It sends no upstream cancellation and does not erase retained order mappings or execution accounting.
- On a subsequent start, reconstruct the active polling registry from retained Submitted, Accepted, and Partial orders and their upstream IDs. Never place an order during recovery.
- Validate the entire recovery snapshot before changing lifecycle state. A missing/invalid upstream ID, mismatched order reference, orphan mapping, duplicate upstream ID, or orphan settlement record blocks startup locally. Do not guess an ID or retry placement.
- A still-running old monitor continues to block startup. Once it exits, the same recovery path applies, including after a shutdown timeout.
- Start polling automatically when recovered orders exist; otherwise preserve lazy monitoring. Repeated start calls must not create another worker or reset accounting.
- Preserve local cash/value on a pending-order resume rather than loading a fresh starting funds snapshot and then applying unaccounted executions to it. Existing periodic account snapshot reconciliation remains separate behavior and is not made transactionally consistent by this change.
- Preserve terminal status and explicit quantity targets. Reset quiet-window time and successful snapshot count on resume so downtime cannot prove unknown-quantity terminal settlement. New valid polling evidence is required.
- Preserve queued notifications, accounted quantities/values, and positions. Duplicate tradebook snapshots after resume must not execute again.

## Work plan

- [x] Add deterministic stop/start regressions with real Backtrader orders and fake transports.
- [x] Verify the regression baseline fails without contacting a deployment.
- [x] Restore validated pending monitoring and reset unknown-quantity settlement evidence.
- [x] Cover clean and timed-out shutdown, invalid mappings, delayed fills, cancellation, idempotence, cash preservation, and a real replacement worker.
- [x] Run the full offline suite with dotenv loading disabled and outbound connections blocked; repeat lifecycle/recovery/settlement/cancellation tests and inspect worker leaks.
- [x] Update compatibility notes and the modernization/task logs.

## Remaining boundaries

- A new Python process or new broker object cannot recover old orders: mappings are memory-only. Do not infer recovery from an account orderbook or silently place replacement orders.
- Existing funds/position snapshots are not an atomic execution ledger. Account-wide cash reconciliation, execution price corrections, truncated tradebooks, API rate limiting, and deliberate paper/live verification remain separate work.
- Unknown fills first visible after terminal cleanup can still be missed; resuming local polling does not remove that quiet-window limitation.
- Python cannot forcibly stop a transport call that outlives the monitor shutdown timeout.
