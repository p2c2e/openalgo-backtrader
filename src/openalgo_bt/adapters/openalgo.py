"""OpenAlgo protocol adapter utilities.

The rest of the package should eventually use this module as the single boundary
for OpenAlgo REST/Python-SDK response shape differences.
"""

from __future__ import annotations

import json
import math
import os
import time
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]

try:
    from openalgo import api as openalgo_api
except Exception:  # pragma: no cover
    openalgo_api = None  # type: ignore[assignment]


class OpenAlgoAPIError(RuntimeError):
    """Base error for OpenAlgo adapter failures."""


class OpenAlgoTransportError(OpenAlgoAPIError):
    """Raised when OpenAlgo cannot be reached or returns invalid transport data."""


class OpenAlgoResponseError(OpenAlgoAPIError):
    """Raised when OpenAlgo returns a response that cannot be normalized."""


class OpenAlgoUnsupportedEndpointError(OpenAlgoResponseError):
    """Raised when a missing endpoint returns the OpenAlgo SPA HTML shell."""


class OpenAlgoAuthError(OpenAlgoAPIError):
    """Raised when OpenAlgo reports authentication failure."""


def _positive_retry_delay(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        delay = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return delay if math.isfinite(delay) and delay > 0 else None


class OpenAlgoRateLimitError(OpenAlgoAPIError):
    """Sanitized throttle evidence with a finite positive retry delay."""

    def __init__(self, retry_after: Any = None):
        super().__init__("OpenAlgo request rate limited")
        self.retry_after = _positive_retry_delay(retry_after) or 60.0


def _http_retry_delay(header: Any) -> float | None:
    delay = _positive_retry_delay(header)
    if delay is not None or not isinstance(header, str):
        return delay
    try:
        date = parsedate_to_datetime(header)
        if date.tzinfo is None:
            return None
        return _positive_retry_delay(date.timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass(frozen=True)
class NormalizedOpenAlgoResponse:
    """Normalized OpenAlgo response envelope."""

    status: Optional[str]
    data: Any
    raw: Any
    source_key: Optional[str] = None

    @property
    def ok(self) -> bool:
        if self.status is None:
            return True
        return str(self.status).lower() in {"success", "ok", "true"}


def _looks_like_html(value: str) -> bool:
    return value.lstrip().lower().startswith(("<!doctype html", "<html", "<head", "<body"))


def _json_loads_if_possible(value: str) -> Any:
    if _looks_like_html(value):
        raise OpenAlgoUnsupportedEndpointError("OpenAlgo returned HTML; endpoint is likely unsupported by this build")
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise OpenAlgoResponseError(f"OpenAlgo returned non-JSON text: {value[:120]!r}") from exc


def normalize_openalgo_response(response: Any) -> NormalizedOpenAlgoResponse:
    """Normalize known OpenAlgo response envelopes.

    Supported shapes:
    - JSON text
    - `{status, data}`
    - `{status, results}`
    - bare lists
    - bare scalar/dict payloads from SDK helpers
    - pandas DataFrame objects, passed through as data
    """
    if isinstance(response, (bytes, bytearray)):
        response = response.decode("utf-8", errors="replace")

    if isinstance(response, str):
        response = _json_loads_if_possible(response)

    if _is_dataframe_like(response):
        return NormalizedOpenAlgoResponse(status="success", data=response, raw=response, source_key=None)

    if isinstance(response, list):
        return NormalizedOpenAlgoResponse(status="success", data=response, raw=response, source_key=None)

    if isinstance(response, dict):
        message = str(response.get("message") or response.get("error") or "")
        status = _status_to_str(response.get("status"))
        successful = status is not None and status.lower() in {"success", "ok", "true"}
        failed = status is not None and not successful
        throttle_code = any(str(response.get(key)) == "429" for key in ("code", "status_code"))
        throttle_message = any(phrase in message.lower() for phrase in ("rate limit exceeded", "too many requests"))
        if not successful and (throttle_code or (failed and throttle_message)):
            raise OpenAlgoRateLimitError(response.get("retry_after"))
        if "invalid openalgo apikey" in message.lower() or "invalid api" in message.lower():
            raise OpenAlgoAuthError(message)

        status = response.get("status")
        if "data" in response:
            return NormalizedOpenAlgoResponse(status=_status_to_str(status), data=response.get("data"), raw=response, source_key="data")
        if "results" in response:
            return NormalizedOpenAlgoResponse(status=_status_to_str(status), data=response.get("results"), raw=response, source_key="results")
        return NormalizedOpenAlgoResponse(status=_status_to_str(status), data=response, raw=response, source_key=None)

    return NormalizedOpenAlgoResponse(status="success", data=response, raw=response, source_key=None)


def normalize_account_payload(response: Any) -> Any:
    """Reject failed account envelopes before publishing diagnostic snapshots."""
    if hasattr(response, "data"):
        status = getattr(response, "status", None)
        if not getattr(response, "ok", True) or (
            status is not None and str(status).lower() not in {"success", "ok", "true"}
        ):
            raise OpenAlgoResponseError("Unsuccessful account snapshot response")
        return response.data
    normalized = normalize_openalgo_response(response)
    if not normalized.ok:
        raise OpenAlgoResponseError("Unsuccessful account snapshot response")
    return normalized.data


def _account_book_rows(response: Any, book: str, container: str) -> list[dict[str, Any]]:
    try:
        rows = normalize_account_payload(response)
    except OpenAlgoAPIError:
        # Account diagnostics must not expose backend messages or payload text.
        raise OpenAlgoResponseError(f"Invalid {book} account response") from None
    if isinstance(rows, dict):
        rows = rows.get(container)
    if not isinstance(rows, list):
        raise OpenAlgoResponseError(f"Invalid {book} list container")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise OpenAlgoResponseError(f"Invalid {book} row {index}: expected object")
    return rows


def _account_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError
    return value


def _account_quantity(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError
    quantity = float(value)
    if not math.isfinite(quantity):
        raise ValueError
    return quantity


def _account_order_id(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return str(value)
    return _account_text(value)


def _account_field(
    row: dict[str, Any], aliases: tuple[str, ...], validator: Callable[[Any], Any],
    book: str, index: int,
) -> Any:
    try:
        values = [validator(row[key]) for key in aliases if key in row]
        if not values or any(value != values[0] for value in values[1:]):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise OpenAlgoResponseError(f"Invalid {book} row {index} field {aliases[0]}") from None
    return values[0]


def normalize_positionbook(response: Any) -> dict[tuple[str, str], float]:
    """Validate all position evidence before projecting signed symbol quantities."""
    book = "positionbook"
    rows = _account_book_rows(response, book, "positions")
    positions: dict[tuple[str, str], float] = {}
    for index, row in enumerate(rows):
        exchange = _account_field(row, ("exchange",), _account_text, book, index)
        symbol = _account_field(row, ("symbol", "tradingsymbol"), _account_text, book, index)
        quantity = _account_field(
            row, ("quantity", "net_quantity", "netqty"), _account_quantity, book, index,
        )
        key = exchange, symbol
        total = positions.get(key, 0.0) + quantity
        if not math.isfinite(total):
            raise OpenAlgoResponseError(f"Invalid {book} row {index} field quantity: aggregate overflow")
        positions[key] = total
    return positions


def normalize_orderbook(response: Any) -> dict[str, dict[str, Any]]:
    """Validate complete diagnostic order rows and reject ambiguous identities."""
    book = "orderbook"
    rows = _account_book_rows(response, book, "orders")
    orders: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        order_id = _account_field(row, ("orderid", "order_id", "id"), _account_order_id, book, index)
        _account_field(row, ("exchange",), _account_text, book, index)
        _account_field(row, ("symbol", "tradingsymbol"), _account_text, book, index)
        quantity = _account_field(row, ("quantity",), _account_quantity, book, index)
        if quantity < 0:
            raise OpenAlgoResponseError(f"Invalid {book} row {index} field quantity")
        action = _account_field(row, ("action",), _account_text, book, index)
        if action.upper() not in {"BUY", "SELL"}:
            raise OpenAlgoResponseError(f"Invalid {book} row {index} field action")
        _account_field(row, ("order_status", "status"), _account_text, book, index)
        if order_id in orders and orders[order_id] != row:
            raise OpenAlgoResponseError(f"Invalid {book} row {index}: conflicting duplicate orderid")
        orders[order_id] = dict(row)
    return orders


def normalize_order_poll_payload(response: Any) -> Any:
    """Reject failed polling envelopes rather than invent empty evidence."""
    if hasattr(response, "data"):
        status = getattr(response, "status", None)
        if not getattr(response, "ok", True) or str(status).lower() in {"error", "failed", "failure"}:
            raise OpenAlgoResponseError("Unsuccessful order polling response")
        return response.data
    normalized = normalize_openalgo_response(response)
    if str(normalized.status).lower() in {"error", "failed", "failure"}:
        raise OpenAlgoResponseError("Unsuccessful order polling response")
    return normalized.data


def normalize_reported_filled_quantity(payload: Any) -> float | None:
    """Read explicit executed quantity; missing is unknown, not requested size."""
    if not isinstance(payload, dict) or "filled_quantity" not in payload:
        return None
    value = payload["filled_quantity"]
    try:
        quantity = float(value) if not isinstance(value, bool) else float("nan")
    except (TypeError, ValueError, OverflowError) as exc:
        raise OpenAlgoResponseError("Invalid reported filled quantity") from exc
    if not math.isfinite(quantity) or quantity < 0:
        raise OpenAlgoResponseError("Invalid reported filled quantity")
    return quantity


def _status_to_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _is_dataframe_like(value: Any) -> bool:
    return hasattr(value, "iterrows") and hasattr(value, "empty") and hasattr(value, "index")


def normalize_history_candles(payload: Any) -> list[dict[str, Any]]:
    """Normalize OpenAlgo historical candle payloads to Backtrader-friendly dicts."""
    if isinstance(payload, NormalizedOpenAlgoResponse):
        payload = payload.data

    if _is_dataframe_like(payload):
        return _history_from_dataframe(payload)

    if isinstance(payload, dict):
        if "candles" in payload:
            payload = payload.get("candles")
        elif "data" in payload or "results" in payload:
            payload = normalize_openalgo_response(payload).data

    if payload is None:
        return []
    if not isinstance(payload, list):
        raise OpenAlgoResponseError(f"Unsupported history payload type: {type(payload).__name__}")

    candles = [_normalize_history_row(row) for row in payload]
    return [candle for candle in candles if candle.get("datetime") is not None]


def _history_from_dataframe(df: Any) -> list[dict[str, Any]]:
    if getattr(df, "empty", False):
        return []
    rows = []
    for idx, row in df.iterrows():
        candle = dict(row.to_dict())
        candle["datetime"] = idx
        rows.append(_normalize_history_row(candle))
    return [row for row in rows if row.get("datetime") is not None]


def _normalize_history_row(row: Any) -> dict[str, Any]:
    if isinstance(row, (list, tuple)):
        timestamp = row[0] if len(row) > 0 else None
        open_ = row[1] if len(row) > 1 else 0.0
        high = row[2] if len(row) > 2 else 0.0
        low = row[3] if len(row) > 3 else 0.0
        close = row[4] if len(row) > 4 else 0.0
        volume = row[5] if len(row) > 5 else 0.0
        oi = row[6] if len(row) > 6 else 0.0
    elif isinstance(row, dict):
        timestamp = row.get("datetime", row.get("timestamp", row.get("time", row.get("date"))))
        open_ = row.get("open", 0.0)
        high = row.get("high", 0.0)
        low = row.get("low", 0.0)
        close = row.get("close", 0.0)
        volume = row.get("volume", 0.0)
        oi = row.get("openinterest", row.get("oi", 0.0))
    else:
        raise OpenAlgoResponseError(f"Unsupported history row type: {type(row).__name__}")

    return {
        "datetime": _coerce_datetime(timestamp),
        "open": _coerce_float(open_),
        "high": _coerce_float(high),
        "low": _coerce_float(low),
        "close": _coerce_float(close),
        "volume": _coerce_float(volume),
        "openinterest": _coerce_float(oi),
    }


def _coerce_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        import pandas as pd  # type: ignore

        if isinstance(value, pd.Timestamp):
            return value.to_pydatetime()
    except Exception:
        pass
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 1e12:
            raw = raw / 1000.0
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return _coerce_datetime(int(text))
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception as exc:
            raise OpenAlgoResponseError(f"Unsupported history timestamp: {value!r}") from exc
    raise OpenAlgoResponseError(f"Unsupported history timestamp type: {type(value).__name__}")


class OpenAlgoAdapter:
    """Small adapter around the OpenAlgo Python client.

    This is intentionally narrow for now. Future phases will move store, feed,
    broker, and direct REST fallback logic behind this class.
    """

    DEFAULT_HOST = "http://127.0.0.1:5000"

    def __init__(
        self,
        api_key: Optional[str] = None,
        host: Optional[str] = None,
        client: Any = None,
        timeout: float = 10.0,
        http_post: Optional[Callable[[str, dict[str, Any], float], Any]] = None,
        *,
        load_env: bool = True,
    ) -> None:
        if load_env and load_dotenv:
            load_dotenv()
        self.api_key = api_key or os.getenv("OPENALGO_API_KEY")
        self.host = host or os.getenv("OPENALGO_API_HOST", self.DEFAULT_HOST)
        self.timeout = float(timeout)
        self._http_post = http_post
        if not self.api_key and client is None:
            raise ValueError("OPENALGO_API_KEY is not set. Configure it in your environment or .env file.")
        self.client = client if client is not None else self._build_client()

    def _build_client(self) -> Any:
        if openalgo_api is None:
            raise OpenAlgoAPIError("openalgo package is not installed")
        return openalgo_api(api_key=self.api_key, host=self.host)

    def normalize(self, response: Any) -> NormalizedOpenAlgoResponse:
        return normalize_openalgo_response(response)

    def call_client(self, method_name: str, **kwargs: Any) -> NormalizedOpenAlgoResponse:
        method = getattr(self.client, method_name, None)
        if method is None:
            raise OpenAlgoUnsupportedEndpointError(f"OpenAlgo client has no method {method_name!r}")
        try:
            return self.normalize(method(**kwargs))
        except OpenAlgoAPIError:
            raise
        except Exception as exc:
            raise OpenAlgoTransportError(f"OpenAlgo client method {method_name!r} failed: {exc}") from exc

    def post(self, endpoint: str, **payload: Any) -> NormalizedOpenAlgoResponse:
        """POST to an OpenAlgo REST endpoint and normalize the response.

        OpenAlgo expects the API key in the JSON body as `apikey`. This method
        is the direct REST fallback for endpoints not covered consistently by
        the Python SDK.
        """
        body = {"apikey": self.api_key}
        body.update(payload)
        url = f"{self.host.rstrip('/')}/api/v1/{endpoint.lstrip('/')}"
        try:
            raw = self._http_post(url, body, self.timeout) if self._http_post else self._post_json(url, body, self.timeout)
            return self.normalize(raw)
        except OpenAlgoAPIError:
            raise
        except Exception as exc:
            raise OpenAlgoTransportError(f"OpenAlgo REST endpoint {endpoint!r} failed: {exc}") from exc

    @staticmethod
    def _post_json(url: str, body: dict[str, Any], timeout: float) -> Any:
        req = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            if exc.code == 429:
                header = exc.headers.get("Retry-After") if exc.headers is not None else None
                delay = _http_retry_delay(header)
                if delay is None:
                    try:
                        payload = json.loads(exc.read().decode("utf-8", errors="replace"))
                        if isinstance(payload, dict):
                            delay = _positive_retry_delay(payload.get("retry_after"))
                    except (TypeError, ValueError, OSError):
                        pass
                raise OpenAlgoRateLimitError(delay) from None
            return exc.read().decode("utf-8", errors="replace")
        except URLError as exc:
            raise OpenAlgoTransportError(str(exc)) from exc

    def ping(self) -> NormalizedOpenAlgoResponse:
        return self.post("ping")

    def intervals(self) -> NormalizedOpenAlgoResponse:
        return self.post("intervals")

    def history(
        self,
        *,
        symbol: str,
        exchange: str,
        interval: str,
        start_date: str,
        end_date: str,
    ) -> NormalizedOpenAlgoResponse:
        return self.post(
            "history",
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )

    def quotes(self, *, symbol: str, exchange: str) -> NormalizedOpenAlgoResponse:
        return self.post("quotes", symbol=symbol, exchange=exchange)

    def multiquotes(self, symbols: list[dict[str, str]]) -> NormalizedOpenAlgoResponse:
        return self.post("multiquotes", symbols=symbols)

    def funds(self) -> NormalizedOpenAlgoResponse:
        return self.post("funds")

    def orderbook(self) -> NormalizedOpenAlgoResponse:
        return self.post("orderbook")

    def tradebook(self) -> NormalizedOpenAlgoResponse:
        return self.post("tradebook")

    def orderstatus(self, *, order_id: str, strategy: Optional[str] = None) -> NormalizedOpenAlgoResponse:
        payload: dict[str, Any] = {"order_id": order_id}
        if strategy:
            payload["strategy"] = strategy
        return self.post("orderstatus", **payload)

    def place_order(self, **payload: Any) -> NormalizedOpenAlgoResponse:
        """Place an order through the documented REST boundary.

        Callers must enforce broker mode and validate payload semantics before
        invoking this method.
        """
        return self.post("placeorder", **payload)

    def cancel_order(self, *, order_id: str, strategy: Optional[str] = None) -> NormalizedOpenAlgoResponse:
        payload: dict[str, Any] = {"order_id": order_id}
        if strategy:
            payload["strategy"] = strategy
        return self.post("cancelorder", **payload)

    def positionbook(self) -> NormalizedOpenAlgoResponse:
        return self.post("positionbook")

    def holdings(self) -> NormalizedOpenAlgoResponse:
        return self.post("holdings")

    def analyzer(self) -> NormalizedOpenAlgoResponse:
        return self.post("analyzer")


__all__ = [
    "NormalizedOpenAlgoResponse",
    "OpenAlgoAdapter",
    "OpenAlgoAPIError",
    "OpenAlgoAuthError",
    "OpenAlgoRateLimitError",
    "OpenAlgoResponseError",
    "OpenAlgoTransportError",
    "OpenAlgoUnsupportedEndpointError",
    "normalize_history_candles",
    "normalize_openalgo_response",
    "normalize_account_payload",
    "normalize_positionbook",
    "normalize_orderbook",
    "normalize_reported_filled_quantity",
    "normalize_order_poll_payload",
]
