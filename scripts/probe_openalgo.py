#!/usr/bin/env python3
"""Probe a local OpenAlgo instance and print a sanitized compatibility report.

This script is intentionally read-only by default. It does not place, modify, or
cancel orders. It requires OPENALGO_API_KEY and optionally uses OPENALGO_API_HOST.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]


READ_ONLY_ENDPOINTS = [
    "ping",
    "intervals",
    "funds",
    "orderbook",
    "tradebook",
    "positionbook",
    "holdings",
    "analyzer",
]


@dataclass
class ProbeConfig:
    api_key: str
    host: str
    symbol: str
    exchange: str
    interval: str
    timeout: float


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_lower = str(key).lower()
            if key_lower in {"apikey", "api_key", "authorization", "token"}:
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _summarize(value: Any, max_items: int = 3) -> Any:
    value = _redact(value)
    if isinstance(value, list):
        return {
            "type": "list",
            "length": len(value),
            "sample": value[:max_items],
        }
    if isinstance(value, dict):
        summary = {
            "type": "dict",
            "keys": sorted(str(key) for key in value.keys()),
        }
        for key in ("status", "message", "error"):
            if key in value:
                summary[key] = value[key]
        for key in ("data", "results"):
            if key in value:
                nested = value[key]
                if isinstance(nested, list):
                    summary[key] = {
                        "type": "list",
                        "length": len(nested),
                        "sample": nested[:max_items],
                    }
                elif isinstance(nested, dict):
                    summary[key] = {
                        "type": "dict",
                        "keys": sorted(str(k) for k in nested.keys()),
                    }
                else:
                    summary[key] = nested
        return summary
    return value


def post_json(config: ProbeConfig, endpoint: str, payload: dict[str, Any] | None = None) -> tuple[bool, Any]:
    body = {"apikey": config.api_key}
    if payload:
        body.update(payload)

    url = f"{config.host.rstrip('/')}/api/v1/{endpoint.lstrip('/')}"
    req = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=config.timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            content_type = resp.headers.get("Content-Type", "")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        return False, {"http_status": exc.code, "body": _parse_body(raw, content_type)}
    except URLError as exc:
        return False, {"transport_error": str(exc)}
    except Exception as exc:
        return False, {"error": repr(exc)}

    return True, _parse_body(raw, content_type)


def _parse_body(raw: str, content_type: str) -> Any:
    stripped = raw.lstrip()
    if stripped.startswith("<"):
        return {
            "type": "html",
            "content_type": content_type,
            "prefix": raw[:160],
        }
    try:
        return json.loads(raw)
    except Exception:
        return {
            "type": "text",
            "content_type": content_type,
            "prefix": raw[:500],
        }


def build_config(args: argparse.Namespace) -> ProbeConfig:
    if load_dotenv:
        load_dotenv()

    api_key = args.api_key or os.getenv("OPENALGO_API_KEY")
    if not api_key:
        raise SystemExit("OPENALGO_API_KEY is required")

    host = args.host or os.getenv("OPENALGO_API_HOST") or "http://127.0.0.1:5000"
    return ProbeConfig(
        api_key=api_key,
        host=host,
        symbol=args.symbol,
        exchange=args.exchange,
        interval=args.interval,
        timeout=args.timeout,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe read-only OpenAlgo API compatibility")
    parser.add_argument("--api-key", default=None, help="OpenAlgo API key. Defaults to OPENALGO_API_KEY")
    parser.add_argument("--host", default=None, help="OpenAlgo host. Defaults to OPENALGO_API_HOST or localhost")
    parser.add_argument("--symbol", default="RELIANCE", help="Symbol to use for quote/history probes")
    parser.add_argument("--exchange", default="NSE", help="Exchange to use for quote/history probes")
    parser.add_argument("--interval", default="1m", help="History interval to probe")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds")
    parser.add_argument("--include-history", action="store_true", help="Also probe history for today's date")
    parser.add_argument("--include-quotes", action="store_true", help="Also probe quotes and multiquotes")
    args = parser.parse_args(argv)

    config = build_config(args)
    report: dict[str, Any] = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "host": config.host,
        "symbol": config.symbol,
        "exchange": config.exchange,
        "interval": config.interval,
        "endpoints": {},
    }

    for endpoint in READ_ONLY_ENDPOINTS:
        ok, body = post_json(config, endpoint)
        report["endpoints"][endpoint] = {
            "ok": ok,
            "summary": _summarize(body),
        }

    if args.include_quotes:
        for endpoint in ("quotes", "multiquotes"):
            payload: dict[str, Any]
            if endpoint == "quotes":
                payload = {"symbol": config.symbol, "exchange": config.exchange}
            else:
                payload = {"symbols": [{"symbol": config.symbol, "exchange": config.exchange}]}
            ok, body = post_json(config, endpoint, payload)
            report["endpoints"][endpoint] = {
                "ok": ok,
                "summary": _summarize(body),
            }

    if args.include_history:
        today = datetime.now().strftime("%Y-%m-%d")
        ok, body = post_json(
            config,
            "history",
            {
                "symbol": config.symbol,
                "exchange": config.exchange,
                "interval": config.interval,
                "start_date": today,
                "end_date": today,
            },
        )
        report["endpoints"]["history"] = {
            "ok": ok,
            "summary": _summarize(body),
        }

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
