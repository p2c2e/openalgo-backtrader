"""Offline cancellation requests, confirmations, and execution-race coverage."""

import threading
from types import SimpleNamespace

import backtrader as bt
import pytest

from openalgo_bt.brokers.oabroker import OABroker, OAOrder


class CancelTransport:
    def __init__(self, fail=False, response=None):
        self.calls = []
        self.fail = fail
        self.response = response
        self.status = "open"
        self.trades = []
        self.on_cancel = None

    def cancel_order(self, **payload):
        self.calls.append(payload)
        if self.on_cancel:
            self.on_cancel()
        if self.fail:
            raise RuntimeError("Offline cancellation transport failure")
        return self.response

    def cancelorder(self, **payload):
        return self.cancel_order(**payload)

    def tradebook(self):
        return SimpleNamespace(data=list(self.trades))

    def orderstatus(self, **payload):
        return SimpleNamespace(data={"order_status": self.status,
                                     "filled_quantity": sum(float(row["quantity"]) for row in self.trades)})


@pytest.fixture
def pending():
    transport = CancelTransport()
    store = SimpleNamespace(adapter=transport, get_client=lambda: None)
    broker = OABroker(store=store, mode="paper", use_funds=False, cash_fallback=1000.0)
    data = SimpleNamespace(close=[100.0])
    # Data feed objects are keys in the broker's local position registry.
    class Data:
        close = data.close

    order = OAOrder("BUY", data=Data(), size=3, price=100.0, simulated=True)
    order.accept(broker)
    broker._orders[order.ref] = order
    broker._oidmap[order.ref] = "order-1"
    broker._active_orders[order.ref] = (order, "order-1")
    broker._reconciled_fills[order.ref] = 0.0
    broker._reconciled_fill_values[order.ref] = 0.0
    broker.start()
    try:
        yield broker, order, transport
    finally:
        broker.stop()


def notifications(broker):
    result = []
    while (order := broker.get_notification()) is not None:
        result.append((order.status, order.executed.size))
    return result


def assert_tracked(broker, order):
    assert broker._orders[order.ref] is order
    assert broker._oidmap[order.ref] == "order-1"
    assert broker._active_orders[order.ref] == (order, "order-1")
    assert order.ref in broker._reconciled_fills
    assert order.ref in broker._reconciled_fill_values


def assert_cleaned(broker, order):
    for registry in (broker._orders, broker._oidmap, broker._active_orders,
                     broker._reconciled_fills, broker._reconciled_fill_values):
        assert order.ref not in registry


@pytest.mark.parametrize("status", [bt.Order.Submitted, bt.Order.Accepted, bt.Order.Partial])
@pytest.mark.parametrize("response", [None, {"status": "success"}, {"status": "error"}])
def test_cancel_request_never_confirms_terminal_state(pending, status, response):
    broker, order, transport = pending
    order.status = status
    transport.response = response

    broker.cancel(order)

    assert transport.calls == [{"order_id": "order-1", "strategy": "Backtrader"}]
    assert order.status == status
    assert_tracked(broker, order)
    assert notifications(broker) == []
    assert broker._monitor_wakeup.is_set()


@pytest.mark.parametrize("raw_client", [False, True])
def test_cancel_failure_keeps_tracking_and_fill_baselines(pending, raw_client):
    broker, order, transport = pending
    transport.fail = True
    if raw_client:
        broker._adapter = None
        broker._client = transport
    broker._apply_execution_delta(order.ref, order, 1, 100.0, terminal=False)
    notifications(broker)

    broker.cancel(order)

    assert order.status == bt.Order.Partial
    assert_tracked(broker, order)
    assert broker._reconciled_fills[order.ref] == 1
    assert broker._reconciled_fill_values[order.ref] == 100.0
    assert broker.getcash() == 900.0
    assert notifications(broker) == []
    expected = {"order_id": "order-1"}
    if not raw_client:
        expected["strategy"] = "Backtrader"
    assert transport.calls == [expected]
    assert broker._monitor_wakeup.is_set()


@pytest.mark.parametrize("missing_id", [False, True])
def test_cancel_without_transport_or_id_does_not_fabricate_cancellation(pending, missing_id):
    broker, order, transport = pending
    if missing_id:
        broker._oidmap.clear()
    else:
        broker._adapter = None
    broker.cancel(order)
    assert order.status == bt.Order.Accepted
    assert broker._orders[order.ref] is order
    assert broker._active_orders[order.ref] == (order, "order-1")
    assert transport.calls == []
    assert notifications(broker) == []


@pytest.mark.parametrize("status", [bt.Order.Completed, bt.Order.Canceled, bt.Order.Rejected,
                                    bt.Order.Expired, bt.Order.Margin])
def test_cancel_ignores_terminal_order(pending, status):
    broker, order, transport = pending
    order.status = status
    broker.cancel(order)
    assert transport.calls == []
    assert notifications(broker) == []
    assert_tracked(broker, order)


def test_cancel_ignores_unowned_order(pending):
    broker, order, transport = pending
    broker._orders.clear()
    broker.cancel(order)
    assert transport.calls == []
    assert notifications(broker) == []


def test_cancel_clone_targets_broker_owned_state(pending):
    broker, order, transport = pending
    clone = order.clone()
    clone.status = bt.Order.Completed
    broker.cancel(clone)
    assert len(transport.calls) == 1
    assert order.status == bt.Order.Accepted
    assert notifications(broker) == []


def test_raw_client_request_keeps_tracking_until_confirmation(pending):
    broker, order, transport = pending
    broker._adapter = None
    broker._client = transport
    broker.cancel(order)
    assert transport.calls == [{"order_id": "order-1"}]
    assert_tracked(broker, order)
    assert notifications(broker) == []


@pytest.mark.parametrize("status", ["cancelled", "canceled"])
def test_partial_cancel_confirmation_accounts_for_late_fill_once(pending, status):
    broker, order, transport = pending
    broker._apply_execution_delta(order.ref, order, 1, 100.0, terminal=False)
    notifications(broker)
    broker.cancel(order)
    transport.status = status
    transport.trades = [{"orderid": "order-1", "tradeid": "fill-1", "quantity": 1, "price": 100},
                        {"orderid": "order-1", "tradeid": "fill-2", "quantity": 1, "price": 110}]

    broker._reconcile_order(order.ref, order, "order-1")
    broker._reconcile_order(order.ref, order, "order-1")

    assert notifications(broker) == [(bt.Order.Partial, 2), (bt.Order.Canceled, 2)]
    assert order.executed.size == 2
    assert order.executed.remsize == 1
    assert order.executed.price == 105.0
    assert broker.getposition(order.data).size == 2
    assert broker.getcash() == 790.0
    assert_cleaned(broker, order)


@pytest.mark.parametrize("status", ["complete", "cancelled"])
def test_full_execution_wins_cancel_race(pending, status):
    broker, order, transport = pending
    broker.cancel(order)
    transport.status = status
    transport.trades = [{"orderid": "order-1", "tradeid": "fill-1", "quantity": 3, "price": 100}]
    broker._reconcile_order(order.ref, order, "order-1")
    assert order.status == bt.Order.Completed
    assert notifications(broker) == [(bt.Order.Completed, 3)]
    assert broker.getcash() == 700.0
    assert_cleaned(broker, order)


@pytest.mark.parametrize("status", ["complete", "cancelled"])
def test_full_fill_already_seen_completes_on_terminal_confirmation(pending, status):
    broker, order, transport = pending
    broker._apply_execution_delta(order.ref, order, 3, 100.0, terminal=False)
    notifications(broker)
    broker.cancel(order)
    transport.status = status
    transport.trades = [{"orderid": "order-1", "tradeid": "fill-1", "quantity": 3, "price": 100}]
    broker._reconcile_order(order.ref, order, "order-1")
    assert order.status == bt.Order.Completed
    assert notifications(broker) == [(bt.Order.Completed, 3)]
    assert broker.getcash() == 700.0
    assert_cleaned(broker, order)


def test_cancel_transport_does_not_hold_state_lock(pending):
    broker, order, transport = pending
    acquired = threading.Event()

    def acquire_from_other_thread():
        with broker._state_lock:
            acquired.set()

    def on_cancel():
        worker = threading.Thread(target=acquire_from_other_thread)
        worker.start()
        try:
            assert acquired.wait(1.0)
        finally:
            worker.join(timeout=1.0)

    transport.on_cancel = on_cancel
    broker.cancel(order)
    assert acquired.is_set()


def test_terminal_confirmation_during_cancel_response_is_not_overwritten(pending):
    broker, order, transport = pending
    transport.status = "complete"
    transport.trades = [{"orderid": "order-1", "tradeid": "fill-1", "quantity": 3, "price": 100}]
    transport.on_cancel = lambda: broker._reconcile_order(order.ref, order, "order-1")
    broker.cancel(order)
    assert order.status == bt.Order.Completed
    assert notifications(broker) == [(bt.Order.Completed, 3)]
    assert_cleaned(broker, order)


def test_open_status_after_cancel_keeps_tracking(pending):
    broker, order, transport = pending
    broker.cancel(order)
    broker._reconcile_order(order.ref, order, "order-1")
    assert order.status == bt.Order.Accepted
    assert notifications(broker) == []
    assert_tracked(broker, order)


@pytest.mark.parametrize("status", ["complete", "cancelled"])
def test_overlapping_terminal_polls_apply_execution_only_once(pending, status):
    broker, order, transport = pending
    rendezvous = threading.Barrier(2)
    failures = []

    def response(orderid):
        rendezvous.wait(timeout=2.0)
        return {"order_status": status, "filled_quantity": 3, "average_price": 100.0}

    def reconcile():
        try:
            broker._reconcile_order(order.ref, order, "order-1")
        except Exception as exc:
            failures.append(exc)

    broker.patched_order_status = response
    workers = [threading.Thread(target=reconcile) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=3.0)
        assert all(not worker.is_alive() for worker in workers)
        assert failures == []
        assert order.status == bt.Order.Completed
        assert notifications(broker) == [(bt.Order.Completed, 3)]
        assert broker.getcash() == 700.0
        assert broker.getposition(order.data).size == 3
        assert_cleaned(broker, order)
    finally:
        rendezvous.abort()
        for worker in workers:
            worker.join(timeout=3.0)


def test_shutdown_during_poll_discards_inflight_response(pending):
    broker, order, transport = pending

    def response(orderid):
        broker.stop()
        return {"order_status": "complete", "filled_quantity": 3, "average_price": 100.0}

    broker.patched_order_status = response
    broker._reconcile_order(order.ref, order, "order-1")
    assert order.status == bt.Order.Accepted
    assert notifications(broker) == []
    assert broker.getcash() == 1000.0
    assert broker.getposition(order.data).size == 0
    assert broker._orders[order.ref] is order
    assert broker._oidmap[order.ref] == "order-1"
    assert broker._active_orders == {}
