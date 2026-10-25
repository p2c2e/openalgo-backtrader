"""Fail-closed routing policy for OpenAlgo broker execution."""

from __future__ import annotations

import os
from typing import Any


class BrokerRoutingError(RuntimeError):
    """Raised when configured broker mode does not permit an upstream order."""


def configured_mode(mode: Any) -> str:
    """Normalize and validate a requested broker routing mode."""
    normalized = str(mode or "backtest").strip().lower()
    if normalized not in {"backtest", "paper", "live"}:
        raise BrokerRoutingError(f"Unsupported broker mode: {normalized!r}")
    return normalized


def validate_routing_mode(
    mode: Any,
    analyzer_data: Any,
    *,
    allow_live: bool = False,
) -> None:
    """Fail closed unless local and remote execution modes are unambiguous."""
    local_mode = configured_mode(mode)
    if local_mode == "backtest":
        raise BrokerRoutingError(
            "Backtest mode does not route upstream orders; enable simulate_fills or select paper mode"
        )
    if not isinstance(analyzer_data, dict):
        raise BrokerRoutingError("OpenAlgo analyzer response must contain an object")

    remote_mode = str(analyzer_data.get("mode", "")).strip().lower()
    analyze_mode = bool(analyzer_data.get("analyze_mode", False))
    if local_mode == "paper":
        if remote_mode not in {"analyze", "paper"} or not analyze_mode:
            raise BrokerRoutingError(
                f"Paper mode requires an OpenAlgo analyze or paper target, got mode={remote_mode!r}"
            )
        return

    if not allow_live or os.getenv("OPENALGO_BT_ALLOW_LIVE") != "1":
        raise BrokerRoutingError(
            "Live routing requires allow_live=True and OPENALGO_BT_ALLOW_LIVE=1"
        )
    if remote_mode != "live" or analyze_mode:
        raise BrokerRoutingError(
            f"Live mode requires an OpenAlgo live target with analyze_mode disabled, got mode={remote_mode!r}"
        )


__all__ = ["BrokerRoutingError", "configured_mode", "validate_routing_mode"]
