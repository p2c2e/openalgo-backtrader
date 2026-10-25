"""Adapter helpers for external OpenAlgo protocol boundaries."""

from openalgo_bt.adapters.openalgo import (
    OpenAlgoAdapter,
    OpenAlgoAPIError,
    OpenAlgoAuthError,
    OpenAlgoRateLimitError,
    OpenAlgoResponseError,
    OpenAlgoTransportError,
    OpenAlgoUnsupportedEndpointError,
    normalize_history_candles,
    normalize_openalgo_response,
)

__all__ = [
    "OpenAlgoAdapter",
    "OpenAlgoAPIError",
    "OpenAlgoAuthError",
    "OpenAlgoRateLimitError",
    "OpenAlgoResponseError",
    "OpenAlgoTransportError",
    "OpenAlgoUnsupportedEndpointError",
    "normalize_history_candles",
    "normalize_openalgo_response",
]
