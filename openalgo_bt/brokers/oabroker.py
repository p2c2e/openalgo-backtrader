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
try:
    # OrderBase provides Backtrader's internal order semantics
    from backtrader import OrderBase as BTOrderBase  # type: ignore
except Exception as _e:  # pragma: no cover
    BTOrderBase = None  # type: ignore

try:
    import backtrader as bt  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError("Backtrader must be installed to use OABroker") from e


class OAOrder(BTOrderBase):  # type: ignore[misc]
    """
    Minimal Backtrader-compatible order carrying standard fields from OrderBase.
    """
    def __init__(self, action: str, **kwargs):
        # Set order direction for Backtrader internals
        self.ordtype = self.Buy if action == "BUY" else self.Sell
        super(OAOrder, self).__init__(**kwargs)


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
    )

    def __init__(self, store: Any):
        super().__init__()
        self._store = store
        self._client = getattr(store, "get_client", lambda: None)()
        self._cash = float(self.p.cash_fallback)
        self._value = float(self.p.cash_fallback)
        # Backtrader expects these attributes for reporting/writers
        self.startingcash = float(self._cash)
        self.startingvalue = float(self._value)
        self._orders = {}  # bt.Order.ref -> backtrader Order
        self._oidmap = {}  # bt.Order.ref -> OpenAlgo order_id (if returned)
        self._started = False
        self._notifs = deque()  # broker notification queue
        # Polling management
        self._poll_threads = {}   # ref -> Thread
        self._poll_stops = {}     # ref -> threading.Event
        # Local position tracking per data feed (for strategy self.position and PnL mgmt)
        self._positions = {}

    # ------------
    # Lifecycle
    # ------------
    def start(self):
        super().start()
        self._started = True
        # Try fetching funds to set starting cash
        try:
            if self.p.use_funds and self._client and hasattr(self._client, "get_funds"):
                funds = self._client.get_funds()
                # Normalize funds to a number if possible
                if isinstance(funds, dict):
                    # Heuristics: look for 'available' or 'cash' or similar
                    for k in ("available", "cash", "net"):
                        if k in funds and isinstance(funds[k], (int, float)):
                            self._cash = float(funds[k])
                            self._value = float(funds[k])
                            break
                elif isinstance(funds, (int, float)):
                    self._cash = float(funds)
                    self._value = float(funds)
        except Exception:
            if self.p.debug:
                traceback.print_exc()

        # Set starting cash/value for writers (use whatever we have after funds check)
        self.startingcash = float(self._cash)
        self.startingvalue = float(self._value)

    def stop(self):
        # Signal all polling threads to stop and join them
        for ref, ev in list(self._poll_stops.items()):
            try:
                ev.set()
            except Exception:
                pass
        for ref, th in list(self._poll_threads.items()):
            try:
                th.join(timeout=2.0)
            except Exception:
                pass
        self._poll_threads.clear()
        self._poll_stops.clear()

        self._started = False
        super().stop()

    # -------------------
    # Account and Values
    # -------------------
    def getcash(self):
        return self._cash

    def setcash(self, amount):
        """
        Set the broker's starting cash. This is required for Backtrader compatibility.
        """
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

    # ------------
    # Order API
    # ------------
    def _data_symbol_exchange(self, data) -> tuple[str, str]:
        # Extract symbol/exchange from data feed if present
        symbol = getattr(getattr(data, "p", None), "symbol", None) or getattr(data, "_name", None) or ""
        exchange = getattr(getattr(data, "p", None), "exchange", None) or self.p.default_exchange
        # If symbol like "NSE:TCS"
        if isinstance(symbol, str) and ":" in symbol:
            parts = symbol.split(":", 1)
            if len(parts) == 2:
                exchange = exchange or parts[0]
                symbol = parts[1]
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
        """
        Try to place an order via OpenAlgo client if available.
        Returns OpenAlgo order_id if known.
        """
        if not self._client:  # or not hasattr(self._client, "place_order"):
            if self.p.debug:
                print("[OABroker] No OpenAlgo client available; rejecting route")
            return None

        symbol, exchange = self._data_symbol_exchange(data)
        size = int(math.fabs(order.size))
        action = "BUY" if isbuy else "SELL"

        pricetype, _ = self._map_exectype(order)
        price = None
        trigger = None

        # Use the order's own constants to avoid cross-class mismatches
        exectype = getattr(order, "exectype", None)
        o_limit = getattr(order, "Limit", object())
        o_stop = getattr(order, "Stop", object())
        o_stoplimit = getattr(order, "StopLimit", object())

        is_limit = exectype == o_limit
        is_stop = exectype == o_stop
        is_stoplimit = exectype == o_stoplimit

        # Determine price/trigger for LIMIT/STOP/STOPLIMIT
        if is_limit:
            if getattr(order, "price", None) is not None:
                try:
                    price = float(order.price)
                except Exception:
                    price = None
        if is_stop or is_stoplimit:
            # Backtrader usually sets trigger in price for Stop/StopLimit and limit in pricelimit for StopLimit
            trig = getattr(order, "price", None)
            if trig is None and getattr(order, "pricelimit", None) is not None:
                trig = order.pricelimit
            try:
                trigger = float(trig) if trig is not None else None
            except Exception:
                trigger = None
            if is_stoplimit:
                lim = getattr(order, "pricelimit", None)
                if lim is None and getattr(order, "price", None) is not None:
                    lim = order.price
                try:
                    price = float(lim) if lim is not None else None
                except Exception:
                    price = None

        try:
            payload = {
                "strategy": self.p.strategy,
                "symbol": symbol,
                "exchange": exchange,
                "action": action,
                "price_type": pricetype,
                "product": self.p.product,
                "quantity": size,
            }
            if price is not None:
                payload["price"] = price
            if trigger is not None:
                payload["trigger_price"] = trigger

            # Try multiple possible client method/param variants
            method = None
            for name in ("placeorder", "place_order", "placeOrder"):
                if hasattr(self._client, name):
                    method = getattr(self._client, name)
                    break
            if method is None:
                if self.p.debug:
                    print("[OABroker] No placeorder method found on client. Available:", [n for n in dir(self._client) if not n.startswith("_")])
                return None

            if self.p.debug:
                print("[OABroker] Placing order payload:", payload, "via", method.__name__)

            # First try with canonical keys
            try:
                resp = method(**payload)
            except TypeError:
                # Fallback aliases for some clients
                alt_payload = dict(payload)
                if "price_type" in alt_payload and "pricetype" not in alt_payload:
                    alt_payload["pricetype"] = alt_payload["price_type"]
                if "quantity" in alt_payload and "qty" not in alt_payload:
                    alt_payload["qty"] = alt_payload["quantity"]
                if "trigger_price" in alt_payload and "triggerprice" not in alt_payload:
                    alt_payload["triggerprice"] = alt_payload["trigger_price"]
                try:
                    resp = method(**alt_payload)
                except Exception as e:
                    if self.p.debug:
                        print("[OABroker] placeorder call failed:", repr(e))
                    raise
            # Try to extract order_id
            order_id = None
            if isinstance(resp, dict):
                for k in ("order_id", "id", "data", "result", "orderid"): # for openalgo, orderid is the right attribute...
                    v = resp.get(k)
                    if isinstance(v, (str, int)):
                        order_id = str(v)
                        break
                    if isinstance(v, dict):
                        # nested id field?
                        for kk in ("order_id", "id", "orderid"):
                            vv = v.get(kk)
                            if isinstance(vv, (str, int)):
                                order_id = str(vv)
                                break
                        if order_id:
                            break
            return order_id
        except Exception:
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
        """
        Request cancellation at OpenAlgo if possible and notify Backtrader.
        """
        # Try to cancel upstream
        try:
            oid = self._oidmap.get(order.ref)
            if oid and self._client and hasattr(self._client, "cancelorder"):
                # OpenAlgo python client uses cancelorder(order_id=...)
                self._client.cancelorder(order_id=str(oid))
        except Exception:
            if self.p.debug:
                traceback.print_exc()

        # Stop polling for this order
        try:
            ev = self._poll_stops.get(order.ref)
            if ev:
                ev.set()
        except Exception:
            pass

        # Update order state locally
        if order.status in (bt.Order.Accepted, bt.Order.Submitted):
            order.cancel()
            self.notify(order)

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

        # Register and notify submitted/accepted
        self._orders[order.ref] = order
        order.submit(self)
        self.notify(order)

        order.accept(self)
        self.notify(order)

        # If simulation mode enabled, fill immediately and return (intended for backtests)
        if self.p.simulate_fills:
            self._simulate_immediate_fill(order)
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
            # Reject if we couldn't obtain an order id
            if self.p.debug:
                print("[OABroker] Rejecting order: no order_id returned from OpenAlgo")
            order.reject(self)
            self.notify(order)
            return order

        # Start polling thread
        stop_ev = threading.Event()
        self._poll_stops[order.ref] = stop_ev
        th = threading.Thread(target=self._poll_order_status, args=(order.ref, order, oa_id, stop_ev), daemon=True)
        self._poll_threads[order.ref] = th
        th.start()

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
        # Called by Cerebro._brokernotify
        if self._notifs:
            return self._notifs.popleft()
        return None

    # Backwards compatibility
    def getnotification(self):
        return self.get_notification()

    def next(self):
        # Optional: mark notification boundary like IBBroker
        self._notifs.append(None)

    # -----------------------------
    # Polling helpers
    # -----------------------------
    def _poll_order_status(self, ref: int, order: bt.Order, oa_id: str, stop_ev: threading.Event):
        """
        Poll OpenAlgo for order status every 2 seconds until filled/cancelled/rejected or stop_ev set.
        """
        while not stop_ev.is_set():
            status = None
            price = None
            try:
                resp = None
                if self._client and hasattr(self._client, "orderstatus"):
                    # Preferred: python client method if available
                    # print("----------->>>>>>")
                    resp = self._client.orderstatus(order_id=str(oa_id), strategy=self.p.strategy) 
                elif self._client and hasattr(self._client, "get_order_status"):
                    # Alternate signature used by some integrations
                    resp = self._client.get_order_status(order_id=str(oa_id), strategy=self.p.strategy)
                elif self._client and hasattr(self._client, "orderbook"):
                    # Fallback: fetch entire orderbook and locate this order id
                    ob = self._client.orderbook()
                    cand = None
                    try:
                        data = ob.get("data") if isinstance(ob, dict) else None
                        orders = data.get("orders") if isinstance(data, dict) else None
                        if isinstance(orders, list):
                            for o in orders:
                                if not isinstance(o, dict):
                                    continue
                                oid = o.get("orderid") or o.get("order_id") or o.get("id")
                                if oid is not None and str(oid) == str(oa_id):
                                    cand = o
                                    break
                    except Exception:
                        cand = None
                    resp = cand
                elif self._client and hasattr(self._client, "get_order_book"):
                    # Alternate: get_order_book variant
                    ob = self._client.get_order_book()
                    cand = None
                    try:
                        data = ob.get("data") if isinstance(ob, dict) else None
                        orders = data.get("orders") if isinstance(data, dict) else None
                        if isinstance(orders, list):
                            for o in orders:
                                if not isinstance(o, dict):
                                    continue
                                oid = o.get("orderid") or o.get("order_id") or o.get("id")
                                if oid is not None and str(oid) == str(oa_id):
                                    cand = o
                                    break
                    except Exception:
                        cand = None
                    resp = cand
                elif self._client and hasattr(self._client, "order_book"):
                    # Alternate: order_book variant
                    ob = self._client.order_book()
                    cand = None
                    try:
                        data = ob.get("data") if isinstance(ob, dict) else None
                        orders = data.get("orders") if isinstance(data, dict) else None
                        if isinstance(orders, list):
                            for o in orders:
                                if not isinstance(o, dict):
                                    continue
                                oid = o.get("orderid") or o.get("order_id") or o.get("id")
                                if oid is not None and str(oid) == str(oa_id):
                                    cand = o
                                    break
                    except Exception:
                        cand = None
                    resp = cand
                else:
                    resp = None

                status, price = self._parse_status(resp)
            except Exception:
                status = None
                if self.p.debug:
                    traceback.print_exc()

            if status in ("FILLED", "FILLED_COMPLETE", "COMPLETE", "COMPLETED", "EXECUTED"):
                # Execute and complete
                exec_price = price if (price is not None) else (order.price if order.price else 0.0)
                try:
                    dt = bt.date2num(datetime.now())
                except Exception:
                    dt = 0.0
                # Update local position and cash accounting
                signed_size = abs(order.size) if order.isbuy() else -abs(order.size)
                pos = self._positions.setdefault(order.data, bt.Position())
                pprice_orig = pos.price
                psize, pprice, opened, closed = pos.update(signed_size, exec_price)

                comminfo = order.comminfo
                openedvalue = comminfo.getoperationcost(opened, exec_price) if comminfo else abs(opened) * exec_price
                closedvalue = comminfo.getoperationcost(closed, pprice_orig) if comminfo else abs(closed) * pprice_orig
                openedcomm = 0.0
                closedcomm = 0.0
                margin = getattr(order.data, "close", [exec_price])[0] if hasattr(order.data, "close") else exec_price
                pnl = 0.0

                # Adjust cash with net trade cash flow at execution price and commission
                try:
                    comm = order.comminfo.getcommission(signed_size, exec_price) if order.comminfo else 0.0
                except Exception:
                    comm = 0.0
                self._cash += -signed_size * exec_price
                self._cash -= comm

                try:
                    order.execute(dt, signed_size, exec_price,
                                  closed, closedvalue, closedcomm,
                                  opened, openedvalue, openedcomm,
                                  margin, pnl,
                                  psize, pprice)
                except Exception:
                    # Fallback: just complete if execute failed
                    pass

                order.completed()
                self.notify(order)
                break

            if status in ("CANCELLED", "CANCELED", "REJECTED", "EXPIRED"):
                if status == "REJECTED":
                    order.reject(self)
                else:
                    order.cancel()
                self.notify(order)
                break

            # keep polling
            stop_ev.wait(2.0)

        # Cleanup
        try:
            self._poll_threads.pop(ref, None)
            self._poll_stops.pop(ref, None)
        except Exception:
            pass

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
