"""
OpenAlgo Broker for Backtrader (oabroker.py)

This implements a minimal Broker interface that integrates with Backtrader and
routes order intents to OpenAlgo if available. It is modeled conceptually after
backtrader/brokers/ibbroker.py but intentionally simplified.

Scope (initial):
- Supports buy/sell submission with basic Market and Limit mapping.
- Attempts to call OpenAlgo client methods if present on the OAStore client:
    - place_order(symbol, quantity, action, exchange, price_type, product, strategy, price=None)
    - cancel_order(order_id, strategy)
    - get_funds()
- Provides getcash()/getvalue() with graceful fallbacks.
- Emits order notifications (Submitted/Accepted/Completed/Rejected/Canceled) for Backtrader.

Notes:
- This is a first-pass broker intended to provide the mechanism. The full Live
  life-cycle (fills from exchange, partials, position sync, margins, etc.) can
  be extended later to reflect actual OpenAlgo account status and websockets.
"""

from __future__ import annotations

import math
import traceback
from typing import Optional, Any
from collections import deque
import threading
import time
from datetime import datetime
from dataclasses import dataclass
try:
    # OrderBase provides Backtrader's internal order semantics
    from backtrader import OrderBase as BTOrderBase  # type: ignore
except Exception as _e:  # pragma: no cover
    BTOrderBase = None  # type: ignore

try:
    import backtrader as bt  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError("Backtrader must be installed to use OABroker") from e
from openalgo_bt.adapters.openalgo import (
    OpenAlgoRateLimitError, OpenAlgoResponseError, normalize_openalgo_response, normalize_account_payload, normalize_order_poll_payload, normalize_reported_filled_quantity,
)
from openalgo_bt.brokers.rate_limit import BrokerRequestDeferred, BrokerRequestLimiter
from openalgo_bt.brokers.routing import BrokerRoutingError, configured_mode, validate_routing_mode
from openalgo_bt.brokers.reconciliation import summarize_order_trades
from openalgo_bt.brokers.account import account_discrepancies, available_cash, orders_by_id, positions_by_symbol
from openalgo_bt.brokers.payload import OrderPayloadError, build_order_payload, extract_order_id




class OAOrder(BTOrderBase):  # type: ignore[misc]
    """
    Minimal Backtrader-compatible order carrying standard fields from OrderBase.
    """
    def __init__(self, action: str, **kwargs):
        # Set order direction for Backtrader internals
        self.ordtype = self.Buy if action == "BUY" else self.Sell
        super(OAOrder, self).__init__(**kwargs)


@dataclass
class _TerminalSettlement:
    status: str
    target: float | None
    quantity: float
    quiet_since: float
    snapshots: int = 1


class OABroker(bt.BrokerBase):
    """
    Minimal OpenAlgo Broker for Backtrader.
    """

    params = dict(
        cash_fallback=1_000_000.0,  # INR fallback if funds API unavailable
        product="MIS",              # default product for intraday
        strategy="Backtrader",      # default strategy tag
        default_exchange="NSE",     # default exchange if not in data params
        debug=False,
        use_funds=True,             # fetch funds from backend on start; disable for backtests
        simulate_fills=False,       # if True, simulate immediate fills (for backtests)
        mode="backtest",           # backtest, paper, or live
        allow_live=False,           # explicit second opt-in for live routing
        terminal_settle_seconds=10.0,  # quiet window when executed quantity is unknown
        api_read_rate=10,
        api_write_rate=5,
    )

    def __init__(self, store: Any):
        super().__init__()
        self._request_limiter = BrokerRequestLimiter(self.p.api_read_rate, self.p.api_write_rate)
        self._monitor_cursor = 0
        self._store = store
        self._client = getattr(store, "get_client", lambda: None)()
        self._adapter = getattr(store, "adapter", None)
        self._cash = float(self.p.cash_fallback)
        self._value = float(self.p.cash_fallback)
        # Backtrader expects these attributes for reporting/writers
        self.startingcash = float(self._cash)
        self.startingvalue = float(self._value)
        self._orders = {}  # bt.Order.ref -> backtrader Order
        self._oidmap = {}  # bt.Order.ref -> OpenAlgo order_id (if returned)
        self._started = False
        self._has_started = False
        self._notifs = deque()  # broker notification queue
        # Polling management
        self._poll_threads = {}   # ref -> Thread
        self._poll_stops = {}     # ref -> threading.Event
        # Local position tracking per data feed (for strategy self.position and PnL mgmt)
        self._positions = {}
        self._reconciled_fills = {}  # ref -> aggregate upstream filled quantity
        self._reconciled_fill_values = {}  # ref -> aggregate upstream filled value
        self._active_orders = {}  # ref -> (order, upstream order id)
        self._terminal_pending: dict[int, _TerminalSettlement] = {}
        settle_seconds = float(self.p.terminal_settle_seconds)
        if not math.isfinite(settle_seconds) or settle_seconds < 0:
            raise ValueError("terminal_settle_seconds must be finite and non-negative")
        self._monitor_stop = threading.Event()
        self._monitor_thread = None
        self._state_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._monitor_interval = 2.0
        self._monitor_backoff = 0.0
        self._monitor_wakeup = threading.Event()
        self._last_account_reconcile = 0.0
        self._account_reconcile_interval = 30.0
        self._account_reconcile_not_before = 0.0
        self._external_positions = {}
        self._external_orders = {}
        self._account_discrepancies = {}
        self._external_cash = None
        self._cash_discrepancy = None
        self._lifecycle_generation = 0

    def _request(self, method, *, write: bool = False, **payload):
        """Admit one attempt outside state locks and never retry transport."""
        self._request_limiter.acquire(write=write)
        try:
            response = method(**payload)
            if isinstance(response, (dict, str, bytes, bytearray)):
                normalize_openalgo_response(response)
            return response
        except OpenAlgoRateLimitError as exc:
            self._request_limiter.defer(exc.retry_after)
            raise

    # ------------
    # Lifecycle
    # ------------
    def _pending_recovery_snapshot(self) -> dict[int, tuple[bt.Order, str]]:
        """Validate retained identity mappings before restoring any monitoring."""
        recovered = {}
        seen_ids = set()
        pending_states = (bt.Order.Submitted, bt.Order.Accepted, bt.Order.Partial)
        for ref, order in self._orders.items():
            if getattr(order, "ref", None) != ref:
                raise RuntimeError("OABroker pending-order recovery: mismatched order reference")
            oid = self._oidmap.get(ref)
            if order.status in pending_states and oid is None:
                raise RuntimeError("OABroker pending-order recovery: missing upstream order ID")
            if oid is not None:
                if not isinstance(oid, str) or not oid.strip() or oid != oid.strip():
                    raise RuntimeError("OABroker pending-order recovery: invalid upstream order ID")
                if oid in seen_ids:
                    raise RuntimeError("OABroker pending-order recovery: duplicate upstream order ID")
                seen_ids.add(oid)
            if order.status in pending_states:
                recovered[ref] = (order, oid)
        if any(ref not in self._orders for ref in self._oidmap):
            raise RuntimeError("OABroker pending-order recovery: orphan upstream order ID")
        if any(recovered.get(ref) != active for ref, active in self._active_orders.items()):
            raise RuntimeError("OABroker pending-order recovery: conflicting active order mapping")
        if any(ref not in recovered for ref in self._terminal_pending):
            raise RuntimeError("OABroker pending-order recovery: orphan terminal settlement")
        return recovered

    def start(self):
        """Initialize once per run and resume retained orders on the same object."""
        with self._lifecycle_lock:
            if self._started:
                return
            if self._monitor_thread is not None and self._monitor_thread.is_alive():
                raise RuntimeError("OABroker monitor is still stopping; retry after it exits")
            with self._state_lock:
                recovered = self._pending_recovery_snapshot() if self._has_started else {}
            super().start()
            self._monitor_thread = None
            self._monitor_stop.clear()
            self._monitor_wakeup.clear()
            self._monitor_backoff = 0.0
            try:
                cash = None
                if not self._has_started and self.p.use_funds:
                    if self._adapter is not None:
                        cash = available_cash(normalize_account_payload(self._request(self._adapter.funds)))
                    elif self._client and hasattr(self._client, "get_funds"):
                        cash = available_cash(normalize_account_payload(self._request(self._client.get_funds)))
                if cash is not None:
                    with self._state_lock:
                        self._cash = cash
                        self._value = cash
                        self._external_cash = cash
                        self._cash_discrepancy = 0.0
            except Exception:
                if self.p.debug:
                    traceback.print_exc()

            with self._state_lock:
                if self._has_started:
                    self._active_orders = recovered
                    now = time.monotonic()
                    for pending in self._terminal_pending.values():
                        # Downtime is not evidence that all executions have arrived.
                        pending.quiet_since = now
                        pending.snapshots = 0
                    if recovered:
                        self._account_reconcile_not_before = now + self._account_reconcile_interval
                self.startingcash = float(self._cash)
                self.startingvalue = float(self._value)
                self._lifecycle_generation += 1
                self._started = True
                self._has_started = True
            if recovered:
                self._ensure_monitor_started()

    def stop(self):
        """Stop local monitoring without cancelling upstream orders."""
        with self._lifecycle_lock:
            with self._state_lock:
                was_started = self._started
                self._started = False
                if was_started:
                    self._lifecycle_generation += 1
                self._monitor_stop.set()
                self._monitor_wakeup.set()
                monitor = self._monitor_thread
            if was_started:
                super().stop()
            if monitor is not None:
                # Do not hold the state lock while the worker exits.
                monitor.join(timeout=2.0)
                if monitor.is_alive():
                    raise RuntimeError("OABroker monitor did not stop within shutdown timeout")
            with self._state_lock:
                self._monitor_thread = None
                self._active_orders.clear()

    def reconcile_account(self) -> None:
        """Publish account diagnostics without rebasing the local execution ledger."""
        with self._state_lock:
            adapter = self._adapter
            if not self._started or adapter is None:
                return
            generation = self._lifecycle_generation
            use_funds = self.p.use_funds

        # Separate endpoint reads have no common execution watermark.
        cash = None
        if use_funds:
            cash = available_cash(normalize_account_payload(self._request(adapter.funds)))
            if cash is None:
                raise OpenAlgoResponseError("Invalid available funds in account snapshot")
        external_positions = positions_by_symbol(self._request(adapter.positionbook))
        external_orders = orders_by_id(self._request(adapter.orderbook))

        with self._state_lock:
            if not self._started or generation != self._lifecycle_generation:
                return
            local_positions: dict[tuple[str, str], float] = {}
            for data, position in self._positions.items():
                symbol, exchange = self._data_symbol_exchange(data)
                key = (exchange, symbol)
                local_positions[key] = local_positions.get(key, 0.0) + float(position.size)
            discrepancies = account_discrepancies(local_positions, external_positions)
            self._external_cash = cash
            self._cash_discrepancy = None if cash is None else cash - self._cash
            self._external_positions = external_positions
            self._external_orders = external_orders
            self._account_discrepancies = discrepancies
            self._last_account_reconcile = time.monotonic()

    # -------------------
    # Account and Values
    # -------------------
    def getcash(self):
        return self._cash

    def get_cash(self):
        return self._cash

    def get_account_cash(self) -> float | None:
        """Return the last observed available funds, not local strategy cash."""
        with self._state_lock:
            return self._external_cash

    def get_cash_discrepancy(self) -> float | None:
        """Return the diagnostic external-minus-local cash difference at refresh."""
        with self._state_lock:
            return self._cash_discrepancy

    def setcash(self, amount):
        """
        Set the broker's starting cash. This is required for Backtrader compatibility.
        """
        with self._state_lock:
            self._cash = float(amount)
            self.startingcash = float(amount)

    def set_coc(self, coc):
        """
        Set cheat-on-close mode for Backtrader compatibility.
        """
        return super().set_coc(coc)

    def get_coc(self):
        """
        Get cheat-on-close mode for Backtrader compatibility.
        """
        return super().get_coc()

    def getvalue(self, datas=None):
        """
        Return portfolio value = cash + MTM of open positions across all datas.
        """
        value = float(self._cash)
        try:
            for data, pos in getattr(self, "_positions", {}).items():
                if not pos or pos.size == 0:
                    continue
                try:
                    last = getattr(data, "close", [0.0])[0]
                except Exception:
                    last = 0.0
                value += float(pos.size) * float(last)
        except Exception:
            pass
        return value

    def get_value(self, datas=None):
        return self.getvalue(datas)
    
    def getcommissioninfo(self, data):
        # Default: Backtrader handles commission via sizers/commission schemes if set
        return super().getcommissioninfo(data)

    def getposition(self, data):
        """
        Return a Position object for the given data feed. Maintains local position state.
        """
        pos = self._positions.get(data)
        if pos is None:
            pos = bt.Position()
            self._positions[data] = pos
        return pos

    def _simulate_immediate_fill(self, order):
        """
        Simulate an immediate fill at current close price for backtesting convenience.
        This triggers order execution/completion and updates local position/cash.
        """
        try:
            exec_price = getattr(order.data, "close", [None])[0]
            if exec_price is None:
                exec_price = float(getattr(order, "price", 0.0) or 0.0)
        except Exception:
            exec_price = float(getattr(order, "price", 0.0) or 0.0)

        try:
            # Use the current bar's datetime instead of real-time datetime for backtesting
            dt = order.data.datetime[0] if hasattr(order.data, 'datetime') else bt.date2num(datetime.now())
        except Exception:
            dt = 0.0

        # Determine signed size (+ for buy, - for sell)
        signed_size = abs(order.size) if order.isbuy() else -abs(order.size)
        pos = self._positions.setdefault(order.data, bt.Position())

        # Capture original position price before update (needed to value closed part)
        pprice_orig = pos.price
        # Update position first to obtain opened/closed breakdown and new psize/pprice
        psize, pprice, opened, closed = pos.update(signed_size, exec_price)

        comminfo = order.comminfo
        openedvalue = comminfo.getoperationcost(opened, exec_price) if comminfo else abs(opened) * exec_price
        closedvalue = comminfo.getoperationcost(closed, pprice_orig) if comminfo else abs(closed) * pprice_orig
        openedcomm = 0.0
        closedcomm = 0.0
        margin = exec_price
        pnl = 0.0

        # Adjust cash with net trade cash flow at execution price and commission
        try:
            comm = order.comminfo.getcommission(signed_size, exec_price) if order.comminfo else 0.0
        except Exception:
            comm = 0.0
        # Selling (negative signed_size) increases cash; buying decreases cash
        self._cash += -signed_size * exec_price
        self._cash -= comm

        try:
            order.execute(dt, signed_size, exec_price,
                          closed, closedvalue, closedcomm,
                          opened, openedvalue, openedcomm,
                          margin, pnl,
                          psize, pprice)
        except Exception:
            # If execute fails, still mark as completed
            pass

        order.completed()
        self.notify(order)
        self._orders.pop(order.ref, None)
        self._oidmap.pop(order.ref, None)

    # ------------
    # Order API
    # ------------
    def _configured_mode(self) -> str:
        """Return the requested routing mode after validating it locally."""
        return configured_mode(getattr(self.p, "mode", "backtest"))

    def _analyzer_mode(self) -> dict[str, Any]:
        """Read the remote OpenAlgo analyzer mode through the adapter boundary."""
        if self._adapter is None:
            raise BrokerRoutingError("OpenAlgo adapter is required for non-simulated order routing")
        response = self._request(self._adapter.analyzer)
        data = getattr(response, "data", response)
        return data

    def validate_routing_mode(self) -> None:
        """Fail closed unless local and remote modes make routing unambiguous."""
        mode = self._configured_mode()
        if mode == "backtest":
            validate_routing_mode(mode, {})
            return
        validate_routing_mode(
            mode,
            self._analyzer_mode(),
            allow_live=bool(getattr(self.p, "allow_live", False)),
        )


    def _data_symbol_exchange(self, data) -> tuple[str, str]:
        symbol = getattr(getattr(data, "p", None), "symbol", None) or getattr(data, "_name", None) or ""
        configured_exchange = getattr(getattr(data, "p", None), "exchange", None)
        parsed_exchange = None
        if isinstance(symbol, str) and ":" in symbol:
            parsed_exchange, symbol = symbol.split(":", 1)
        exchange = configured_exchange or parsed_exchange or self.p.default_exchange
        return str(symbol), str(exchange)

    def _map_exectype(self, order) -> tuple[str, Optional[float]]:
        """
        Map Backtrader execution type to OpenAlgo price_type using the order's own constants.
        This avoids cross-class constant mismatches (bt.Order vs OrderBase subclasses).
        """
        try:
            exectype = getattr(order, "exectype", None)
            # Resolve constants from the order instance itself
            o = order
            if exectype is None:
                return "MARKET", None
            if exectype in (getattr(o, "Market", object()), getattr(o, "MarketClose", object())):
                return "MARKET", None
            if exectype == getattr(o, "Limit", object()):
                return "LIMIT", None
            if exectype == getattr(o, "Stop", object()):
                return "SL-M", None
            if exectype == getattr(o, "StopLimit", object()):
                return "SL", None
        except Exception:
            pass
        # Fallback
        return "MARKET", None

    def _place_openalgo_order(self, order: bt.Order, data, isbuy: bool) -> Optional[str]:
        """Validate and route an order through the adapter when available."""
        symbol, exchange = self._data_symbol_exchange(data)
        price_type, _ = self._map_exectype(order)
        exectype = getattr(order, "exectype", None)
        is_limit = exectype == getattr(order, "Limit", object())
        is_stop = exectype == getattr(order, "Stop", object())
        is_stoplimit = exectype == getattr(order, "StopLimit", object())
        price = getattr(order, "price", None) if is_limit else None
        trigger_price = getattr(order, "price", None) if (is_stop or is_stoplimit) else None
        if is_stoplimit:
            price = getattr(order, "pricelimit", None) or trigger_price

        try:
            payload = build_order_payload(
                strategy=self.p.strategy,
                product=self.p.product,
                symbol=symbol,
                exchange=exchange,
                action="BUY" if isbuy else "SELL",
                quantity=abs(order.size),
                price_type=price_type,
                price=price,
                trigger_price=trigger_price,
            )
            adapter = self._adapter
            if adapter is not None:
                return extract_order_id(self._request(adapter.place_order, write=True, **payload))
            if self._client is not None and hasattr(self._client, "placeorder"):
                return extract_order_id(self._request(self._client.placeorder, write=True, **payload))
        except (OrderPayloadError, Exception):
            if self.p.debug:
                traceback.print_exc()
        return None

    # BrokerBase calls: buy/sell -> place order
    def _makeorder(self, action, owner, data,
                   size, price=None, plimit=None,
                   exectype=None, valid=None, tradeid=0, **kwargs):
        """
        Create a Backtrader order object using OAOrder (OrderBase subclass).
        """
        if BTOrderBase is None:
            raise RuntimeError("Backtrader OrderBase not available")
        order = OAOrder(
            action,
            owner=owner,
            data=data,
            size=size,
            price=price,
            pricelimit=plimit,
            exectype=exectype,
            valid=valid,
            tradeid=tradeid,
            **kwargs
        )
        order.addcomminfo(self.getcommissioninfo(data))
        return order

    def buy(self, owner, data,
            size, price=None, plimit=None,
            exectype=None, valid=None, tradeid=0,
            **kwargs):
        order = self._makeorder('BUY', owner, data, size, price, plimit, exectype, valid, tradeid, **kwargs)
        return self.submit(order)

    def sell(self, owner, data,
             size, price=None, plimit=None,
             exectype=None, valid=None, tradeid=0,
             **kwargs):
        order = self._makeorder('SELL', owner, data, size, price, plimit, exectype, valid, tradeid, **kwargs)
        return self.submit(order)

    def cancel(self, order: bt.Order):
        """Request upstream cancellation; polling confirms the terminal state."""
        with self._state_lock:
            tracked = self._orders.get(order.ref)
            if tracked is None or tracked.status not in (
                bt.Order.Submitted, bt.Order.Accepted, bt.Order.Partial,
            ):
                return
            oid = self._oidmap.get(tracked.ref)
            adapter = self._adapter
            client = self._client
            if not oid:
                return

        # Transport acknowledgement is not proof that the unfilled remainder was cancelled.
        try:
            if adapter is not None:
                self._request(adapter.cancel_order, write=True, order_id=str(oid), strategy=self.p.strategy)
            elif client is not None and hasattr(client, "cancelorder"):
                self._request(client.cancelorder, write=True, order_id=str(oid))
        except Exception:
            if self.p.debug:
                traceback.print_exc()
        finally:
            self._monitor_wakeup.set()

    # -----------------------------
    # Internal submit/notification
    # -----------------------------
    def submit(self, order):
        """
        Submit an OAOrder to the broker and attempt routing via OpenAlgo.

        This will:
          - notify Submitted and Accepted
          - attempt place_order
          - if order_id returned, start a polling thread to check status every 2s
          - if no order_id, reject the order
        """
        if not self._started:
            self.start()

        # Register and notify submission. Acceptance is emitted only after a
        # simulated fill path or a confirmed upstream order identifier.
        self._orders[order.ref] = order
        order.submit(self)
        self.notify(order)

        # If simulation mode enabled, fill immediately and return (intended for backtests)
        if self.p.simulate_fills:
            order.accept(self)
            self.notify(order)
            self._simulate_immediate_fill(order)
            return order

        try:
            self.validate_routing_mode()
        except (BrokerRoutingError, BrokerRequestDeferred, OpenAlgoRateLimitError):
            order.reject(self)
            self.notify(order)
            self._orders.pop(order.ref, None)
            return order

        # Try to route upstream via OpenAlgo
        oa_id = None
        try:
            isbuy = order.isbuy()
            oa_id = self._place_openalgo_order(order, order.data, isbuy)
            if oa_id:
                self._oidmap[order.ref] = oa_id
        except Exception:
            if self.p.debug:
                traceback.print_exc()

        if not oa_id:
            if self.p.debug:
                print("[OABroker] Rejecting order: no order_id returned from OpenAlgo")
            order.reject(self)
            self.notify(order)
            self._orders.pop(order.ref, None)
            return order

        order.accept(self)
        self.notify(order)

        self._active_orders[order.ref] = (order, oa_id)
        self._ensure_monitor_started()

        return order

    # ---------------
    # Notifications
    # ---------------
    def notify(self, order):
        # Enqueue order notifications for Cerebro
        try:
            o = order.clone()  # clone to avoid side-effects
        except Exception:
            o = order
        self._notifs.append(o)

    def get_notification(self):
        """Return the next queued broker notification for Cerebro."""
        try:
            return self._notifs.popleft()
        except IndexError:
            return None

    # Backwards compatibility
    def getnotification(self):
        return self.get_notification()

    def next(self):
        """Backtrader broker hook. Notifications are drained directly by Cerebro."""
        return None



    def patched_order_status(self, orderid):
        """Keep status quantity separate from tradebook execution evidence."""
        adapter = getattr(self, "_adapter", None)
        transport = adapter if adapter is not None else self._client
        status_response = self._request(transport.orderstatus, order_id=str(orderid), strategy="BackTrader Polling")
        data = normalize_order_poll_payload(status_response)
        reported_quantity = normalize_reported_filled_quantity(data)
        trades = normalize_order_poll_payload(self._request(transport.tradebook))
        if not isinstance(trades, list):
            raise OpenAlgoResponseError("Tradebook must contain a list of executions")
        summary = summarize_order_trades(trades, str(orderid))
        if not summary.valid:
            raise OpenAlgoResponseError("Tradebook contains invalid execution evidence")
        if isinstance(data, dict):
            patched = dict(data)
        elif isinstance(data, str):
            patched = {"order_status": data}
        else:
            raise OpenAlgoResponseError("Order status must contain an order payload")
        patched["reported_filled_quantity"] = reported_quantity
        patched["filled_quantity"] = summary.quantity
        patched["average_price"] = summary.average_price
        if patched.get("order_status", "").lower() == "complete" and summary.average_price is not None:
            patched["price"] = summary.average_price
        return patched
    # -----------------------------
    # Polling helpers
    # -----------------------------
    @staticmethod
    def _filled_quantity(response: Any) -> float:
        """Extract an aggregate upstream filled quantity from a status response."""
        data = response.get("data", response) if isinstance(response, dict) else response
        quantity = normalize_reported_filled_quantity(data)
        return quantity if quantity is not None else 0.0

    def _apply_execution_delta(
        self,
        ref: int,
        order: bt.Order,
        aggregate_quantity: float,
        price: float,
        *,
        terminal: bool,
    ) -> bool:
        """Apply only newly observed aggregate tradebook quantity to Backtrader."""
        if not math.isfinite(aggregate_quantity) or aggregate_quantity < 0:
            raise OpenAlgoResponseError("Invalid aggregate execution quantity")
        if aggregate_quantity > 0 and (not math.isfinite(price) or price <= 0):
            raise OpenAlgoResponseError("Invalid aggregate execution price")
        previous = self._reconciled_fills.get(ref, 0.0)
        target = min(abs(float(order.size)), max(previous, aggregate_quantity))
        delta = target - previous
        if delta <= 0:
            if terminal and target >= abs(float(order.size)) and order.status != bt.Order.Completed:
                order.completed()
                self.notify(order)
                return True
            return False

        previous_value = self._reconciled_fill_values.get(ref, 0.0)
        target_value = price * target
        delta_price = (target_value - previous_value) / delta

        if not math.isfinite(target_value) or not math.isfinite(delta_price) or delta_price <= 0:
            raise OpenAlgoResponseError("Invalid incremental execution value")
        signed_size = delta if order.isbuy() else -delta
        pos = self._positions.setdefault(order.data, bt.Position())
        original_price = pos.price
        psize, pprice, opened, closed = pos.update(signed_size, delta_price)
        comminfo = order.comminfo
        openedvalue = comminfo.getoperationcost(opened, delta_price) if comminfo else abs(opened) * delta_price
        closedvalue = comminfo.getoperationcost(closed, original_price) if comminfo else abs(closed) * original_price
        try:
            commission = comminfo.getcommission(signed_size, delta_price) if comminfo else 0.0
        except Exception:
            commission = 0.0
        self._cash += -signed_size * delta_price
        self._cash -= commission

        try:
            dt = bt.date2num(datetime.now())
        except Exception:
            dt = 0.0
        margin = getattr(order.data, "close", [delta_price])[0] if hasattr(order.data, "close") else delta_price
        order.execute(
            dt, signed_size, delta_price,
            closed, closedvalue, 0.0,
            opened, openedvalue, 0.0,
            margin, 0.0, psize, pprice,
        )
        self._reconciled_fills[ref] = target
        self._reconciled_fill_values[ref] = target_value
        if terminal and target >= abs(float(order.size)):
            order.completed()
        else:
            order.partial()
        self.notify(order)
        return True


    def _ensure_monitor_started(self) -> None:
        with self._state_lock:
            if not self._started or self._monitor_stop.is_set():
                return
            if self._monitor_thread is not None and self._monitor_thread.is_alive():
                return
            self._monitor_thread = threading.Thread(target=self._monitor_orders, daemon=True)
            self._monitor_thread.start()
            self._monitor_wakeup.set()

    def _reconcile_order(self, ref: int, order: bt.Order, oa_id: str) -> None:
        with self._state_lock:
            if self._monitor_stop.is_set() or self._active_orders.get(ref) != (order, oa_id):
                return
        try:
            response = self.patched_order_status(orderid=str(oa_id))
            status, _ = self._parse_status(response)
        except (BrokerRequestDeferred, OpenAlgoRateLimitError):
            raise
        except Exception:
            if self.p.debug:
                traceback.print_exc()
            return

        with self._state_lock:
            # Another reconciliation or shutdown can invalidate an in-flight response.
            if self._monitor_stop.is_set() or self._active_orders.get(ref) != (order, oa_id):
                return
            quantity = self._filled_quantity(response)
            data = response.get("data", response) if isinstance(response, dict) else {}
            reported = data.get("reported_filled_quantity") if isinstance(data, dict) else None
            if reported is not None:
                reported = normalize_reported_filled_quantity({"filled_quantity": reported})
            complete_states = {"FILLED", "FILLED_COMPLETE", "COMPLETE", "COMPLETED", "EXECUTED"}
            terminal_states = complete_states | {"CANCELLED", "CANCELED", "REJECTED", "EXPIRED"}
            pending = self._terminal_pending.get(ref)
            now = time.monotonic()
            if status in terminal_states:
                target = reported
                if status in complete_states:
                    target = max(abs(float(order.size)), target or 0.0)
                new_terminal = pending is None
                if new_terminal:
                    pending = _TerminalSettlement(status, target, quantity, now)
                    self._terminal_pending[ref] = pending
                else:
                    # Completion evidence dominates cancellation of a fully filled order.
                    if status in complete_states:
                        pending.status = status
                    if target is not None:
                        pending.target = max(pending.target or 0.0, target)
            if pending is not None and not (status in terminal_states and new_terminal):
                if pending.snapshots == 0 or quantity != pending.quantity:
                    pending.quantity = quantity
                    pending.quiet_since = now
                    pending.snapshots = 1
                else:
                    pending.snapshots += 1

            previous = self._reconciled_fills.get(ref, 0.0)
            contradictory = pending is not None and pending.target is not None and pending.target > abs(float(order.size))
            full = pending is not None and not contradictory and max(previous, quantity) >= abs(float(order.size))
            # Status prices are not execution evidence; never invent a requested-price fill.
            exec_price = data.get("average_price") if isinstance(data, dict) else None
            if quantity > previous and (not isinstance(exec_price, (int, float)) or isinstance(exec_price, bool)):
                raise OpenAlgoResponseError("Missing tradebook execution price")
            self._apply_execution_delta(ref, order, quantity, exec_price or 0.0, terminal=full)
            if pending is None or contradictory:
                return
            if full:
                self._cleanup_order(ref)
                return
            if pending.status in complete_states:
                return
            if pending.target is not None:
                settled = quantity >= max(pending.target, previous)
            else:
                settled = (
                    pending.snapshots >= 2
                    and quantity >= self._reconciled_fills.get(ref, 0.0)
                    and now - pending.quiet_since >= self.p.terminal_settle_seconds
                )
            if not settled:
                return
            if pending.status == "REJECTED":
                order.reject(self)
            elif pending.status == "EXPIRED":
                order.expire()
            else:
                order.cancel()
            self.notify(order)
            self._cleanup_order(ref)
    def _monitor_orders(self) -> None:
        while not self._monitor_stop.is_set():
            with self._state_lock:
                active = list(self._active_orders.items())
                if active:
                    start = self._monitor_cursor % len(active)
                    active = active[start:] + active[:start]
            had_error = False
            processed = 0
            for ref, (order, oa_id) in active:
                if self._monitor_stop.is_set():
                    break
                try:
                    self._reconcile_order(ref, order, oa_id)
                except (BrokerRequestDeferred, OpenAlgoRateLimitError):
                    # Leave the first deferred order first in the next monitor cycle.
                    had_error = True
                    break
                except Exception:
                    had_error = True
                    if self.p.debug:
                        traceback.print_exc()
                processed += 1
            if active:
                self._monitor_cursor = (start + processed) % len(active)
            if (
                not self._monitor_stop.is_set()
                and self._adapter is not None
                and time.monotonic() - self._last_account_reconcile >= self._account_reconcile_interval
                and time.monotonic() >= self._account_reconcile_not_before
            ):
                try:
                    self.reconcile_account()
                except Exception:
                    had_error = True
                    if self.p.debug:
                        traceback.print_exc()
            if had_error:
                self._monitor_backoff = min(30.0, max(1.0, self._monitor_backoff * 2 or 1.0))
            else:
                self._monitor_backoff = 0.0
            wait_for = max(self._monitor_interval, self._monitor_backoff)
            self._monitor_wakeup.wait(wait_for)
            self._monitor_wakeup.clear()

    def _cleanup_order(self, ref: int) -> None:
        lock = getattr(self, "_state_lock", None)
        if lock is None:
            self._active_orders.pop(ref, None)
            self._orders.pop(ref, None)
            self._oidmap.pop(ref, None)
            self._reconciled_fills.pop(ref, None)
            self._reconciled_fill_values.pop(ref, None)
            self._terminal_pending.pop(ref, None)
            return
        with lock:
            self._active_orders.pop(ref, None)
            self._orders.pop(ref, None)
            self._oidmap.pop(ref, None)
            self._reconciled_fills.pop(ref, None)
            self._reconciled_fill_values.pop(ref, None)
            self._terminal_pending.pop(ref, None)

    def _poll_order_status(self, ref: int, order: bt.Order, oa_id: str, stop_ev: threading.Event):
        """Deprecated compatibility shim; monitoring is centralized per broker."""
        with self._state_lock:
            self._active_orders[order.ref] = (order, oa_id)
        self._monitor_wakeup.set()
        self._ensure_monitor_started()

    @staticmethod
    def _parse_status(resp: Any) -> tuple[Optional[str], Optional[float]]:
        """
        Attempt to parse a status string and executed price from the response.
        Returns (status, price)
        """
        status = None
        price = None
        d = resp
        try:
            # Common containers
            if isinstance(d, dict) and "data" in d:
                d = d["data"]
            # If still dict, look for status/price-ish fields
            if isinstance(d, dict):
                for k in ("status", "order_status", "state", "Status"):
                    v = d.get(k)
                    if isinstance(v, str):
                        status = v.strip().upper()
                        break
                for k in ("price", "avg_price", "average_price", "fill_price", "executed_price"):
                    v = d.get(k)
                    if isinstance(v, (int, float)):
                        price = float(v)
                        break
            elif isinstance(d, str):
                status = d.strip().upper()
        except Exception:
            pass
        return status, price
