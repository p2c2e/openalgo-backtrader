"""Normalization helpers for OpenAlgo account and position snapshots."""

from __future__ import annotations

import math
from typing import Any

from openalgo_bt.adapters.openalgo import normalize_orderbook, normalize_positionbook


def available_cash(payload: Any) -> float | None:
    """Extract available cash from a normalized funds response without guessing."""
    data = _data(payload)
    if not isinstance(data, dict):
        return None
    for key in ("availablecash", "available_cash", "available", "cash", "net"):
        if key not in data:
            continue
        raw = data[key]
        if isinstance(raw, bool):
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        return value if math.isfinite(value) else None
    return None


def positions_by_symbol(payload: Any) -> dict[tuple[str, str], float]:
    """Project only fully validated position snapshots at the adapter boundary."""
    return normalize_positionbook(payload)


def orders_by_id(payload: Any) -> dict[str, dict[str, Any]]:
    """Project only fully validated order snapshots at the adapter boundary."""
    return normalize_orderbook(payload)


def account_discrepancies(
    local_positions: dict[tuple[str, str], float],
    external_positions: dict[tuple[str, str], float],
    *,
    tolerance: float = 0.0,
) -> dict[tuple[str, str], tuple[float, float]]:
    """Return symbols whose local and external quantities disagree."""
    keys = set(local_positions) | set(external_positions)
    return {
        key: (local_positions.get(key, 0.0), external_positions.get(key, 0.0))
        for key in keys
        if abs(local_positions.get(key, 0.0) - external_positions.get(key, 0.0)) > tolerance
    }


def _data(payload: Any) -> Any:
    return getattr(payload, "data", payload)


__all__ = ["available_cash", "positions_by_symbol", "orders_by_id", "account_discrepancies"]
