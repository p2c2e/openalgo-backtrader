"""Offline terminal status with delayed, regressing, or invalid trade evidence."""

from types import SimpleNamespace

import backtrader as bt
import pytest

from openalgo_bt.brokers.oabroker import OABroker, OAOrder


class Transport:
    def __init__(self):
        self.status = {"order_status": "cancelled"}
        self.trades = []
        self.calls = []
        self.fail = False
        self.raw = False

    def orderstatus(self, **payload):
        self.calls.append("status")
        return {"data": dict(self.status)} if self.raw else SimpleNamespace(data=dict(self.status))

    def tradebook(self):
        self.calls.append("trades")
        if self.fail:
            raise RuntimeError("Offline tradebook unavailable")
        return {"data": self.trades} if self.raw else SimpleNamespace(data=self.trades)


class Data:
    close = [100.0]


@pytest.fixture
def pending(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr("openalgo_bt.brokers.oabroker.time.monotonic", lambda: clock.now)
    transport = Transport()
    broker = OABroker(store=SimpleNamespace(adapter=transport, get_client=lambda: None),
                      mode="paper", use_funds=False, cash_fallback=1000.0)
    order = OAOrder("BUY", data=Data(), size=3, price=100.0, simulated=True)
    order.accept(broker)
    broker._orders[order.ref] = order
    broker._oidmap[order.ref] = "order-1"
    broker._active_orders[order.ref] = (order, "order-1")
    broker.start()
    try:
        yield broker, order, transport, clock
    finally:
        broker.stop()


def poll(pending, at):
    broker, order, _, clock = pending
    clock.now = at
    broker._reconcile_order(order.ref, order, "order-1")


def fill(quantity, price=100.0, tradeid="fill-1"):
    return {"orderid": "order-1", "tradeid": tradeid, "quantity": quantity, "price": price}


def notifications(broker):
    result = []
    while (order := broker.get_notification()) is not None:
        result.append((order.status, order.executed.size))
    return result


def assert_cleaned(broker, order):
    for registry in (broker._orders, broker._oidmap, broker._active_orders,
                     broker._reconciled_fills, broker._reconciled_fill_values, broker._terminal_pending):
        assert order.ref not in registry


@pytest.mark.parametrize("raw", [False, True])
def test_explicit_quantity_waits_for_delayed_trades_beyond_quiet_window(pending, raw):
    broker, order, transport, _ = pending
    transport.raw = raw
    if raw:
        broker._adapter = None
        broker._client = transport
    transport.status["filled_quantity"] = "2"
    poll(pending, 0)
    poll(pending, 100)
    assert order.status == bt.Order.Accepted
    assert order.ref in broker._active_orders
    assert broker.getcash() == 1000.0
    transport.trades = [fill(1)]
    poll(pending, 101)
    assert order.status == bt.Order.Partial
    assert order.ref in broker._active_orders
    transport.trades = [fill(1), fill(1, 110.0, "fill-2")]
    poll(pending, 102)
    poll(pending, 103)
    assert notifications(broker) == [(bt.Order.Partial, 1), (bt.Order.Partial, 2), (bt.Order.Canceled, 2)]
    assert order.executed.price == 105.0
    assert broker.getcash() == 790.0
    assert broker.getposition(order.data).size == 2
    assert_cleaned(broker, order)


@pytest.mark.parametrize("status, expected", [("cancelled", bt.Order.Canceled),
                                             ("rejected", bt.Order.Rejected),
                                             ("expired", bt.Order.Expired)])
def test_unknown_quantity_quiet_window_resets_on_new_fills(pending, status, expected):
    broker, order, transport, _ = pending
    transport.status = {"order_status": status, "quantity": "3"}
    poll(pending, 0)
    assert notifications(broker) == []
    transport.trades = [fill(1)]
    poll(pending, 9)
    poll(pending, 10)
    assert order.status == bt.Order.Partial
    transport.trades = [fill(1), fill(1, 110.0, "fill-2")]
    poll(pending, 18)
    poll(pending, 27)
    assert order.ref in broker._active_orders
    poll(pending, 28)
    assert notifications(broker) == [(bt.Order.Partial, 1), (bt.Order.Partial, 2), (expected, 2)]
    assert broker.getcash() == 790.0
    assert_cleaned(broker, order)


def test_full_late_fill_wins_cancelled_status(pending):
    broker, order, transport, _ = pending
    poll(pending, 0)
    transport.status = {"order_status": "open"}
    transport.trades = [fill(3)]
    poll(pending, 1)
    assert order.status == bt.Order.Completed
    assert notifications(broker) == [(bt.Order.Completed, 3)]
    assert broker.getcash() == 700.0
    assert_cleaned(broker, order)


def test_terminal_evidence_survives_transient_open_status(pending):
    broker, order, transport, _ = pending
    transport.status["filled_quantity"] = 2
    poll(pending, 0)
    transport.status = {"order_status": "open", "filled_quantity": 0}
    poll(pending, 30)
    assert order.ref in broker._active_orders
    transport.trades = [fill(2)]
    poll(pending, 31)
    assert order.status == bt.Order.Canceled
    assert order.executed.size == 2
    assert_cleaned(broker, order)


def test_complete_status_always_waits_for_full_tradebook_quantity(pending):
    broker, order, transport, _ = pending
    transport.status = {"order_status": "complete", "filled_quantity": 1}
    transport.trades = [fill(1)]
    poll(pending, 0)
    poll(pending, 100)
    assert order.ref in broker._active_orders
    transport.trades = [fill(3)]
    poll(pending, 101)
    assert order.status == bt.Order.Completed
    assert_cleaned(broker, order)


@pytest.mark.parametrize("value", ["bad", -1, True, float("nan"), float("inf")])
def test_malformed_explicit_quantity_never_confirms_empty_cancellation(pending, value):
    broker, order, transport, _ = pending
    transport.status["filled_quantity"] = value
    poll(pending, 0)
    poll(pending, 100)
    assert order.status == bt.Order.Accepted
    assert notifications(broker) == []
    assert order.ref in broker._active_orders


@pytest.mark.parametrize("trades", [None, {}, [fill(1, float("nan"))],
                                   [fill(1, 0)], [fill(float("inf"))],
                                   [fill("bad")], [fill(1, "bad")]])
def test_invalid_tradebook_does_not_settle_or_poison_accounting(pending, trades):
    broker, order, transport, _ = pending
    transport.trades = trades
    poll(pending, 0)
    poll(pending, 100)
    assert order.status == bt.Order.Accepted
    assert broker.getcash() == 1000.0
    assert notifications(broker) == []
    assert order.ref in broker._active_orders


def test_tradebook_errors_do_not_create_terminal_snapshots(pending):
    broker, order, transport, _ = pending
    transport.fail = True
    poll(pending, 0)
    poll(pending, 100)
    transport.fail = False
    poll(pending, 101)
    assert order.status == bt.Order.Accepted
    poll(pending, 111)
    assert order.status == bt.Order.Canceled
    assert_cleaned(broker, order)


def test_explicit_zero_confirms_unfilled_cancel_immediately(pending):
    broker, order, transport, _ = pending
    transport.status["filled_quantity"] = "0"
    poll(pending, 0)
    assert notifications(broker) == [(bt.Order.Canceled, 0)]
    assert_cleaned(broker, order)


def test_contradictory_explicit_quantity_above_order_size_stays_pending(pending):
    broker, order, transport, _ = pending
    transport.status["filled_quantity"] = 4
    transport.trades = [fill(1)]
    poll(pending, 0)
    poll(pending, 100)
    assert order.status == bt.Order.Partial
    assert order.ref in broker._active_orders


def test_patched_status_reads_status_before_tradebook_and_keeps_quantity_separate(pending):
    broker, _, transport, _ = pending
    transport.status["filled_quantity"] = "2"
    transport.trades = [fill(1)]
    response = broker.patched_order_status("order-1")
    assert transport.calls == ["status", "trades"]
    assert response["filled_quantity"] == 1
    assert response["reported_filled_quantity"] == 2


def test_regressing_trade_snapshot_does_not_revalue_already_accounted_fills(pending):
    broker, order, transport, _ = pending
    transport.status = {"order_status": "open"}
    transport.trades = [fill(2, 100)]
    poll(pending, 0)
    transport.trades = [fill(1, 200)]
    poll(pending, 1)
    transport.trades = [fill(3, 110)]
    transport.status = {"order_status": "complete"}
    poll(pending, 2)
    assert order.executed.price == 110.0
    assert broker.getcash() == 670.0
    assert_cleaned(broker, order)


def test_unknown_empty_quantity_requires_quiet_successful_snapshots(pending):
    broker, order, _, _ = pending
    poll(pending, 0)
    poll(pending, 9)
    assert order.ref in broker._active_orders
    poll(pending, 10)
    assert notifications(broker) == [(bt.Order.Canceled, 0)]
    assert_cleaned(broker, order)


def test_zero_quiet_window_still_requires_two_snapshots(pending):
    broker, order, _, _ = pending
    broker.p.terminal_settle_seconds = 0.0
    poll(pending, 0)
    assert order.ref in broker._active_orders
    poll(pending, 0)
    assert notifications(broker) == [(bt.Order.Canceled, 0)]
    assert_cleaned(broker, order)


def test_terminal_target_only_increases_until_evidence_catches_up(pending):
    broker, order, transport, _ = pending
    transport.status["filled_quantity"] = 1
    poll(pending, 0)
    transport.status["filled_quantity"] = 2
    poll(pending, 1)
    transport.status["filled_quantity"] = 0
    transport.trades = [fill(1)]
    poll(pending, 100)
    assert order.ref in broker._active_orders
    transport.trades = [fill(2)]
    poll(pending, 101)
    assert notifications(broker) == [(bt.Order.Partial, 1), (bt.Order.Partial, 2), (bt.Order.Canceled, 2)]
    assert broker.getcash() == 800.0
    assert_cleaned(broker, order)


def test_regressing_unknown_snapshot_cannot_confirm_terminal_cleanup(pending):
    broker, order, transport, _ = pending
    transport.trades = [fill(2)]
    poll(pending, 0)
    transport.trades = [fill(1)]
    poll(pending, 10)
    poll(pending, 20)
    assert order.ref in broker._active_orders
    assert order.executed.size == 2
    assert broker.getcash() == 800.0
    transport.trades = [fill(2)]
    poll(pending, 21)
    assert order.ref in broker._active_orders
    poll(pending, 31)
    assert notifications(broker) == [(bt.Order.Partial, 2), (bt.Order.Canceled, 2)]
    assert_cleaned(broker, order)


@pytest.mark.parametrize("raw", [False, True])
def test_failed_envelope_does_not_count_as_a_settlement_snapshot(pending, monkeypatch, raw):
    broker, order, transport, _ = pending
    poll(pending, 0)
    real_tradebook = transport.tradebook
    failure = {"status": "error", "data": []} if raw else SimpleNamespace(status="error", data=[])
    monkeypatch.setattr(transport, "tradebook", lambda: failure)
    poll(pending, 100)
    assert broker._terminal_pending[order.ref].snapshots == 1
    assert order.status == bt.Order.Accepted
    monkeypatch.setattr(transport, "tradebook", real_tradebook)
    poll(pending, 101)
    assert order.status == bt.Order.Canceled
    assert_cleaned(broker, order)


@pytest.mark.parametrize("status, expected", [("rejected", bt.Order.Rejected),
                                             ("expired", bt.Order.Expired)])
def test_explicit_partial_target_preserves_non_cancel_terminal_state(pending, status, expected):
    broker, order, transport, _ = pending
    transport.status = {"order_status": status, "filled_quantity": 1}
    poll(pending, 0)
    transport.trades = [fill(1)]
    poll(pending, 100)
    assert notifications(broker) == [(bt.Order.Partial, 1), (expected, 1)]
    assert broker.getcash() == 900.0
    assert_cleaned(broker, order)


@pytest.mark.parametrize("seconds", [-1, float("nan"), float("inf")])
def test_invalid_terminal_quiet_window_is_rejected(seconds):
    with pytest.raises(ValueError, match="terminal_settle_seconds"):
        OABroker(store=SimpleNamespace(get_client=lambda: None),
                 use_funds=False, terminal_settle_seconds=seconds)


def test_explicit_target_cannot_confirm_a_regressed_trade_snapshot(pending):
    broker, order, transport, _ = pending
    transport.status = {"order_status": "open"}
    transport.trades = [fill(2)]
    poll(pending, 0)
    transport.status = {"order_status": "cancelled", "filled_quantity": 1}
    transport.trades = [fill(1)]
    poll(pending, 100)
    assert order.ref in broker._active_orders
    transport.trades = [fill(2)]
    poll(pending, 101)
    assert notifications(broker) == [(bt.Order.Partial, 2), (bt.Order.Canceled, 2)]
    assert broker.getcash() == 800.0
    assert_cleaned(broker, order)
