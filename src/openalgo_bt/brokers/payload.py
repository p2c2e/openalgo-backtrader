"""Validated mapping from Backtrader orders to OpenAlgo REST order payloads."""

from __future__ import annotations

from typing import Any


class OrderPayloadError(ValueError):
    """Raised when an order cannot be represented safely by OpenAlgo."""


def build_order_payload(
    *,
    strategy: str,
    product: str,
    symbol: str,
    exchange: str,
    action: str,
    quantity: Any,
    price_type: str,
    price: Any = None,
    trigger_price: Any = None,
) -> dict[str, Any]:
    """Validate and return the canonical OpenAlgo order payload."""
    normalized_action = str(action).upper()
    normalized_type = str(price_type).upper()
    if normalized_action not in {"BUY", "SELL"}:
        raise OrderPayloadError(f"Unsupported action: {action!r}")
    if normalized_type not in {"MARKET", "LIMIT", "SL-M", "SL"}:
        raise OrderPayloadError(f"Unsupported price type: {price_type!r}")
    if not isinstance(symbol, str) or not symbol.strip():
        raise OrderPayloadError("symbol is required")
    if not isinstance(exchange, str) or not exchange.strip():
        raise OrderPayloadError("exchange is required")
    if not isinstance(product, str) or not product.strip():
        raise OrderPayloadError("product is required")
    if not isinstance(strategy, str) or not strategy.strip():
        raise OrderPayloadError("strategy is required")

    try:
        normalized_quantity = int(quantity)
    except (TypeError, ValueError) as exc:
        raise OrderPayloadError("quantity must be a positive integer") from exc
    if normalized_quantity <= 0:
        raise OrderPayloadError("quantity must be a positive integer")

    payload: dict[str, Any] = {
        "strategy": strategy,
        "symbol": symbol,
        "exchange": exchange,
        "action": normalized_action,
        "price_type": normalized_type,
        "product": product,
        "quantity": normalized_quantity,
    }
    if normalized_type in {"LIMIT", "SL"}:
        payload["price"] = _positive_number("price", price)
    elif price is not None:
        payload["price"] = _positive_number("price", price)
    if normalized_type in {"SL-M", "SL"}:
        payload["trigger_price"] = _positive_number("trigger_price", trigger_price)
    elif trigger_price is not None:
        payload["trigger_price"] = _positive_number("trigger_price", trigger_price)
    return payload


def extract_order_id(response: Any) -> str | None:
    """Extract a stable upstream order identifier from normalized response data."""
    data = getattr(response, "data", response)
    return _extract(data)


def _extract(value: Any) -> str | None:
    if isinstance(value, (str, int)):
        return str(value)
    if not isinstance(value, dict):
        return None
    for key in ("order_id", "orderid", "id"):
        candidate = value.get(key)
        if isinstance(candidate, (str, int)):
            return str(candidate)
    for key in ("data", "result"):
        candidate = _extract(value.get(key))
        if candidate:
            return candidate
    return None


def _positive_number(name: str, value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise OrderPayloadError(f"{name} must be a positive number") from exc
    if number <= 0:
        raise OrderPayloadError(f"{name} must be a positive number")
    return number


__all__ = ["OrderPayloadError", "build_order_payload", "extract_order_id"]
