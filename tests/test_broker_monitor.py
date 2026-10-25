import threading
from types import SimpleNamespace

from openalgo_bt.brokers.oabroker import OABroker


class Order:
    def __init__(self, size=2):
        self.size = size
        self.price = 100.0
        self.data = object()
        self.rejected = False
        self.cancelled = False

    def reject(self, broker):
        self.rejected = True

    def cancel(self):
        self.cancelled = True


def make_broker():
    broker = object.__new__(OABroker)
    broker._state_lock = threading.RLock()
    broker._monitor_stop = SimpleNamespace(is_set=lambda: False)
    broker._active_orders = {}
    broker._orders = {}
    broker._oidmap = {}
    broker._reconciled_fills = {}
    broker._reconciled_fill_values = {}
    broker._terminal_pending = {}
    broker._positions = {}
    broker._cash = 1_000.0
    broker.p = SimpleNamespace(debug=False)
    broker.notify = lambda order: None
    return broker


def test_monitor_reconciles_terminal_order_and_cleans_active_state():
    broker = make_broker()
    order = Order()
    broker._active_orders[1] = (order, "order-1")
    broker._orders[1] = order
    broker._oidmap[1] = "order-1"
    broker.patched_order_status = lambda orderid: {
        "order_status": "complete",
        "filled_quantity": 2,
        "average_price": 100.0,
        "price": 100.0,
    }
    applied = []
    broker._apply_execution_delta = lambda *args, **kwargs: applied.append((args, kwargs)) or True

    broker._reconcile_order(1, order, "order-1")

    assert len(applied) == 1
    assert 1 not in broker._active_orders
    assert 1 not in broker._orders
    assert 1 not in broker._oidmap


def test_monitor_keeps_order_active_when_partial_status_has_no_new_fill():
    broker = make_broker()
    order = Order()
    broker._active_orders[1] = (order, "order-1")
    broker._orders[1] = order
    broker._oidmap[1] = "order-1"
    broker.patched_order_status = lambda orderid: {"order_status": "open", "filled_quantity": 0}

    broker._reconcile_order(1, order, "order-1")

    assert broker._active_orders[1] == (order, "order-1")
