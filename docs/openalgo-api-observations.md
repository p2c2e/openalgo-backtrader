# OpenAlgo API Observations

This document records sanitized evidence captured from the intentional local OpenAlgo target. It describes observed behavior, not a compatibility guarantee for every server or broker backend.

## Target

| Field | Observed value |
| --- | --- |
| REST host | `http://127.0.0.1:5000` |
| Broker backend | `zerodha` |
| REST credential field | `apikey` |
| Probe date | 2026-08-31 |

The service was first contacted with a stale inherited environment credential. The project `.env` credential succeeded after the inherited `OPENALGO_API_KEY` and `OPENALGO_API_HOST` values were cleared for the probe process. Normal environment variables intentionally take precedence over `.env` values.

## Read-only REST Endpoint Results

All endpoints below returned HTTP 200 with a top-level `status: "success"` envelope.

| Endpoint | Envelope | Observed `data` or `results` shape |
| --- | --- | --- |
| `/api/v1/ping` | `data` | object with `broker`, `message` |
| `/api/v1/intervals` | `data` | object with interval lists by granularity |
| `/api/v1/history` | `data` | list of OHLCV dictionaries |
| `/api/v1/quotes` | `data` | quote object with bid/ask/LTP/OI/volume fields |
| `/api/v1/multiquotes` | `results` | list of quote results |
| `/api/v1/funds` | `data` | object with available cash, collateral, and margin fields |
| `/api/v1/orderbook` | `data` | object with `orders`, `statistics` |
| `/api/v1/tradebook` | `data` | list of trades |
| `/api/v1/positionbook` | `data` | list of positions |
| `/api/v1/holdings` | `data` | object with `holdings`, `statistics` |
| `/api/v1/analyzer` | `data` | object with mode and analysis fields |

## Verified History Shape

A one-day, one-minute `NSE:RELIANCE` request returned 360 candles. Each observed candle included:

```text
close, high, low, oi, open, timestamp, volume
```

`timestamp` was an integer. The adapter's support for epoch timestamps and `oi` normalization is therefore relevant to this target.

## Verified Intervals

The endpoint reported:

| Granularity | Values |
| --- | --- |
| Minutes | `1m`, `3m`, `5m`, `10m`, `15m`, `30m`, `60m` |
| Hours | `1h` |
| Days | `D` |
| Months, weeks, seconds | No values reported |

`OAStore` now uses runtime interval discovery. Backtrader `1.9.78.123` represents hourly bars as `TimeFrame.Minutes` with compression `60`; the observed native `60m` interval is selected directly.

## Analyzer Observation and Safety Implication

The analyzer initially reported `mode: "live"` and `analyze_mode: false`. On 2026-08-31 it was switched to `mode: "analyze"` and `analyze_mode: true`, which matches the new paper-routing guard. This is still **not** permission to send orders: no order probe will run until supported order lifecycle and reconciliation behavior is implemented and tested.

## Remaining Verification

- Websocket URL, authentication field, acknowledgement, subscription, heartbeat, error, and reconnect messages.
- Empty, validation-error, authentication-error, rate-limit, and transport response fixtures.
- Order-placement request and lifecycle behavior, using an explicitly approved paper-safe route only.
- Backend behavior for additional exchanges, instruments, and order types.
