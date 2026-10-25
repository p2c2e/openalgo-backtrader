"""Pure helpers for reconciling OpenAlgo tradebook rows with broker order state."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class TradeSummary:
    """Deduplicated execution summary for one upstream order."""

    quantity: float
    average_price: float | None
    trade_count: int
    valid: bool = True


def summarize_order_trades(trades: Iterable[Any], order_id: str) -> TradeSummary:
    """Return a conservative quantity and weighted price for one order.

    Only rows with a stable execution identity are deduplicated. Rows without
    one are retained because dropping them could understate a live position.
    """
    seen: set[tuple[str, str]] = set()
    quantity = 0.0
    weighted_value = 0.0
    count = 0
    valid = True

    for row in trades:
        if not isinstance(row, dict):
            valid = False
            continue
        if str(row.get("orderid")) != str(order_id):
            continue
        identity = _trade_identity(row)
        if identity is not None and identity in seen:
            continue
        trade_quantity = _number(row.get("quantity", row.get("filled_quantity")))
        price = _number(row.get("average_price", row.get("price")))
        if trade_quantity is None or trade_quantity <= 0 or price is None or price <= 0:
            valid = False
            continue
        if identity is not None:
            seen.add(identity)
        quantity += trade_quantity
        weighted_value += trade_quantity * price
        count += 1

    valid = valid and math.isfinite(quantity) and math.isfinite(weighted_value)
    return TradeSummary(
        quantity=quantity,
        average_price=(weighted_value / quantity) if quantity else None,
        trade_count=count,
        valid=valid,
    )


def _trade_identity(row: dict[str, Any]) -> tuple[str, str] | None:
    for key in ("tradeid", "trade_id", "execution_id", "fill_id"):
        value = row.get(key)
        if value not in (None, ""):
            return key, str(value)
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


__all__ = ["TradeSummary", "summarize_order_trades"]
