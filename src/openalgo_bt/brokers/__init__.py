"""Broker integration exports."""

from .routing import BrokerRoutingError

try:
    from .oabroker import OABroker
except RuntimeError:  # Backtrader is optional for policy-only consumers.
    OABroker = None  # type: ignore[assignment]

__all__ = ["BrokerRoutingError", "OABroker"]
