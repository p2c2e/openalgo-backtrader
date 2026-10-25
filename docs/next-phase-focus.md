# Next Phase Focus: Feature Completeness and Testing

**Date:** 2026-08-29
**Current Version:** `0.1.11`, targeting `0.2.0`
**Status:** Phase 0 complete (planning, adapter scaffolding, 11 tests passing). Phase A and B (contract verification, protocol consolidation) are blockers for Phase 1 merge.

## Executive Summary

The `openalgo-backtrader` integration is **incomplete and untested for production use**. The current implementation has:

- ✅ Basic adapter structure and 11 offline tests
- ✅ Historical OHLCV normalization sketched
- ❌ **No live websocket tests** (auth field uncertain: `api_key` vs `apikey`)
- ❌ **No broker order lifecycle tests** (placement, rejection, fills, cancellation untested)
- ❌ **No reconnect or resilience tests** (websocket hub, polling thread, circuit-breaker missing)
- ❌ **No production safety guards** (broker mode not enforced, no analyzer-mode validation)

## Critical Gaps by Component

### 1. Live Feed (`OAData` + websocket)

**Current state:** Prototype with shared-hub connection pooling; `_WSConnection` connects and subscribes but lacks:

| Gap | Impact | Priority |
| --- | ------ | -------- |
| Websocket auth field name (`api_key` vs `apikey`) | Silent auth failure | **CRITICAL** |
| No reconnect loop or backoff | Feed dies on network hiccup | **HIGH** |
| No state machine (connecting/authed/subscribed/stale) | Impossible to diagnose failures | **HIGH** |
| Tick aggregation bucket alignment | Bars may not align to market hours | **MEDIUM** |
| No fake-websocket tests | Cannot test without live server | **HIGH** |

**Next action:** Run `scripts/probe_openalgo.py` against a live OpenAlgo instance to capture websocket auth/subscribe/heartbeat payloads, then add fake-websocket tests.

---

### 2. Broker Order Lifecycle (`OABroker`)

**Current state:** Skeleton with `submit()`, `getposition()`, `getcash()`; lacks:

| Gap | Impact | Priority |
| --- | ------ | -------- |
| No order lifecycle tests (place→accept→fill, reject, cancel) | Silent order failures | **CRITICAL** |
| No tradebook reconciliation | Unknown if fills were received | **HIGH** |
| Polling thread never exits cleanly | Thread leaks on shutdown | **HIGH** |
| No analyzer-mode guard | May route live orders during backtest | **CRITICAL** |
| No partial-fill handling | Double-count or miss fills | **HIGH** |
| Failure on transport error silently loses state | Order status unknown after reconnect | **MEDIUM** |

**Next action:** Add fake OpenAlgo client tests (mock tradebook/order-status), test order payload mapping, and reconciliation logic WITHOUT a live broker.

---

### 3. Historical Data and Intervals

**Current state:** Adapter normalizes OHLCV payloads; interval discovery is a stub:

| Gap | Impact | Priority |
| --- | ------ | -------- |
| No runtime interval discovery | Unsupported intervals fail silently | **HIGH** |
| No resampling tests (1m→5m, 15m, 1h) | Bars may drift or duplicate | **HIGH** |
| `utils/history.py` creates client at import | Hardcoded paths block refactor | **MEDIUM** |
| No empty/malformed payload tests | Edge cases silently produce bad data | **MEDIUM** |

**Next action:** Add interval discovery to adapter, test local 1m→Xm aggregation, isolate `utils/history.py`.

---

### 4. Production Safety

**Current state:** No mode guards or state validation:

| Gap | Impact | Priority |
| --- | ------ | -------- |
| Broker mode not enforced (backtest/paper/live) | Live orders possible during backtest | **CRITICAL** |
| No analyzer-mode validation at startup | Mode mismatch undetected | **CRITICAL** |
| Broad exception handling with silent failures | Silent data loss or order non-submission | **HIGH** |
| No circuit-breaker for API failures | Rapid retry storms possible | **MEDIUM** |

**Next action:** Add `mode` parameter validation, read OpenAlgo analyzer mode on broker init, refuse mode mismatches.

---

## Immediate Next Steps (Recommended Order)

### 🔴 Critical Path (Do First — Blocks Merge)

**Phase A: Contract Verification**

```bash
# 1. Capture live OpenAlgo behavior
python scripts/probe_openalgo.py  # Requires running OpenAlgo instance
# → Record: websocket auth field, subscribe/unsub payloads, heartbeat, tick format

# 2. Document findings
# → docs/openalgo-api-observations.md (new file)
# → Update docs/compatibility.md with live evidence

# 3. Add sanitized fixtures
# → tests/fixtures/openalgo/websocket_auth.json
# → tests/fixtures/openalgo/websocket_tick.json
```

**Phase B1: Broker Safety Guards**

```bash
# 1. Add mode enforcement to OABroker.__init__
# → mode ∈ {backtest, paper, live}
# → default to 'backtest'

# 2. Add analyzer-mode guard at startup
# → Read OpenAlgo /analyzer endpoint on init
# → Refuse live routing if mode mismatch
# → Log both sides of the mismatch

# 3. Comprehensive order-lifecycle tests with fake client
# → tests/test_broker_lifecycle.py (new file)
# → Tests: place order, receive order_id, poll status, fill, cancel, reject
```

**Phase B2: Live Feed Websocket**

```bash
# 1. Fix websocket auth field if needed (from Phase A evidence)
# → Likely: api_key → apikey

# 2. Add stateful websocket hub
# → Connected / Connecting / Authenticated / Subscribed / Stale / Reconnecting / Failed
# → Exponential backoff reconnect with jitter
# → Resubscribe on reconnect

# 3. Add fake-websocket tests
# → tests/test_feed_live_ws.py (new file)
# → Tests: auth, subscribe, unsub, reconnect, stale detection
```

---

### 🟡 High Priority (Phase 1 Merge, Target `0.2.0`)

1. **Historical interval discovery** (`OAStore.supported_intervals()`)
   - Call `/api/v1/intervals` and cache result
   - Fallback policy: use native interval, else derive from 1m, else error
   - Test 1m→5m, 15m, 1h local aggregation

2. **Isolate `utils/history.py`**
   - Remove import-time client creation
   - Add `OPENALGO_BT_CACHE_DIR` support
   - Make cache optional

3. **Test coverage for critical paths**
   - Broker lifecycle: 15+ tests
   - Websocket behavior: 12+ fake-WS tests
   - Historical normalization: 8+ edge-case tests

---

### 🟢 Medium Priority (Phase 2 Polish, Target `0.3.0`)

- Stream stale-detection and recovery
- Optional REST reconciliation for gaps
- Paper-trading multi-symbol soak test
- Structured logging and metrics

---

## Definition of "Feature Complete"

The package is **ready for 0.2.0 release** when:

1. ✅ All websocket payloads are verified against live OpenAlgo and captured in fixtures
2. ✅ Broker mode is enforced; live routing refused when mode!=live
3. ✅ Order lifecycle (place, fill, cancel, reject) is covered by 15+ fake-client tests
4. ✅ Historical bars are deterministic for empty, malformed, and time-boundary cases
5. ✅ Reconnect and stale-detection have automated tests
6. ✅ Build verified: `uv sync && uv run --extra dev pytest -q` passes all tests in <5s
7. ✅ README updated with tested feature matrix and known limitations

---

## Test Running Checklist

```bash
# After each change:

# 1. Build and dependency sync
uv sync

# 2. Run full offline test suite
uv run --extra dev pytest -q
# Expected: all tests pass, <1s runtime

# 3. Verify no coverage regressions
# (Add coverage threshold check when CI is ready)

# 4. Lint and type-check (when ready)
# uv run mypy src/ --strict
# uv run ruff check src/ tests/

# 5. Pre-push: sanity check
git status --short
git diff --name-only @{u}..HEAD | grep -v docs/ | grep -v .md
# → Only src/ and tests/ should have code changes
```

---

## Success Criteria by Phase

### Phase A (Contract) — 3–5 days

- [ ] `scripts/probe_openalgo.py` run against live target
- [ ] Websocket auth field confirmed (api_key or apikey)
- [ ] Sanitized fixtures captured in `tests/fixtures/openalgo/*.json`
- [ ] `docs/openalgo-api-observations.md` written
- [ ] `docs/compatibility.md` updated with live evidence

### Phase B1 (Broker Safety) — 5–7 days

- [ ] `OABroker` accepts `mode` parameter
- [ ] Analyzer-mode validation at startup
- [ ] 15+ fake-client lifecycle tests passing
- [ ] Order payload validation tests passing

### Phase B2 (Websocket) — 5–7 days

- [ ] Websocket auth field corrected (if needed)
- [ ] Stateful hub with reconnect backoff
- [ ] 12+ fake-websocket tests passing
- [ ] Stale-detection behavior documented

### Phase 1 (Historical) — 3–5 days

- [ ] Interval discovery + fallback policy implemented
- [ ] `utils/history.py` isolated (no import-time client)
- [ ] 8+ historical edge-case tests passing
- [ ] Local resampling tests passing

### 0.2.0 Release Gate

- [ ] All above phases complete
- [ ] Full test suite passes: `uv run --extra dev pytest -q`
- [ ] README feature matrix matches test coverage
- [ ] No uncommitted changes
- [ ] Ready to tag and document

---

## File Changes Summary

| File | Purpose | Status |
| --- | --- | --- |
| `src/openalgo_bt/adapters/openalgo.py` | Protocol boundary | Scaffold ✅; expand for WS, broker |
| `src/openalgo_bt/feeds/oa.py` | Live data | Add state machine, reconnect |
| `src/openalgo_bt/brokers/oabroker.py` | Order routing | Add mode guard, analyzer check |
| `tests/test_broker_lifecycle.py` | NEW | Fake-client order tests |
| `tests/test_feed_live_ws.py` | NEW | Fake-websocket tests |
| `tests/fixtures/openalgo/*.json` | NEW | Verified payloads from Phase A probe |
| `docs/openalgo-api-observations.md` | NEW | Live contract evidence |
| `docs/next-phase-focus.md` | THIS FILE | Action plan and gate checklist |

---

## References

- **Planning:** `docs/updatation-plan.md` (full phase roadmap)
- **Task tracking:** `docs/task-log.md` (daily work log)
- **Compatibility:** `docs/compatibility.md` (tested matrix)
- **AGENTS.md:** `AGENTS.md` (repo conventions for future agents)
- **Build/test:** `README.md` (setup and run commands)

---

**Next review:** After Phase A and B1 are complete. Checkmark off items as they finish, then proceed to Phase B2 and Phase 1.
