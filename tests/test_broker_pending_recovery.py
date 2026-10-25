"""Same-object broker recovery uses retained IDs, never replacement orders."""

import threading
from types import SimpleNamespace

import backtrader as bt
import pytest

from openalgo_bt.brokers.oabroker import OABroker, OAOrder


class Transport:
    def __init__(self):
        self.status = {"order_status": "open", "filled_quantity": 0}
        self.trades = []
        self.funds_calls = 0
        self.fail = False
        self.status_ids = []

    def funds(self):
        self.funds_calls += 1
        return SimpleNamespace(data={"availablecash": 5000.0})

    def orderstatus(self, **payload):
        self.status_ids.append(payload["order_id"])
        if self.fail:
            raise RuntimeError("Offline status unavailable")
        return SimpleNamespace(data=dict(self.status))

    def tradebook(self):
        return SimpleNamespace(data=list(self.trades))

    def place_order(self, **payload):
        pytest.fail("Recovery must never resubmit an upstream order")

    def cancel_order(self, **payload):
        pytest.fail("Local lifecycle must never cancel an upstream order")


class Data:
    close = [100.0]


def notifications(broker):
    result = []
    while (order := broker.get_notification()) is not None:
        result.append((order.status, order.executed.size))
    return result


def trade(quantity, price=100.0, tradeid="fill-1"):
    return {"orderid": "order-1", "tradeid": tradeid, "quantity": quantity, "price": price}


@pytest.fixture
def pending(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr("openalgo_bt.brokers.oabroker.time.monotonic", lambda: clock.now)
    adapter = Transport()
    broker = OABroker(store=SimpleNamespace(adapter=adapter, get_client=lambda: None),
                      mode="paper", use_funds=False, cash_fallback=1000.0)
    launches = []
    monkeypatch.setattr(broker, "_ensure_monitor_started", lambda: launches.append(True))
    broker.start()
    order = OAOrder("BUY", data=Data(), size=3, price=100.0, simulated=True)
    order.accept(broker)
    broker._orders[order.ref] = order
    broker._oidmap[order.ref] = "order-1"
    broker._active_orders[order.ref] = (order, "order-1")
    try:
        yield broker, order, adapter, clock, launches
    finally:
        broker.stop()


def poll(pending):
    broker, order, _, _, _ = pending
    broker._reconcile_order(order.ref, order, "order-1")


@pytest.mark.parametrize("status", [bt.Order.Submitted, bt.Order.Accepted, bt.Order.Partial])
def test_resume_restores_pending_ids_and_starts_monitor_once(pending, status):
    broker, order, adapter, _, launches = pending
    order.status = status
    broker.notify(order)
    broker.stop()
    assert broker._active_orders == {}
    broker.start()
    broker.start()
    assert broker._active_orders == {order.ref: (order, "order-1")}
    assert launches == [True]
    assert notifications(broker) == [(status, 0)]
    assert adapter.status_ids == []


def test_resume_accounts_only_new_fills_and_preserves_cash(pending):
    broker, order, adapter, _, _ = pending
    broker.p.use_funds = True
    adapter.status["filled_quantity"] = 1
    adapter.trades = [trade(1)]
    poll(pending)
    assert broker.getcash() == 900.0
    broker.stop()
    broker.start()
    assert adapter.funds_calls == 0
    assert broker.startingcash == broker.getcash() == 900.0
    poll(pending)
    adapter.status = {"order_status": "complete", "filled_quantity": 3}
    adapter.trades = [trade(1), trade(2, 110.0, "fill-2")]
    poll(pending)
    assert notifications(broker) == [(bt.Order.Partial, 1), (bt.Order.Completed, 3)]
    assert broker.getposition(order.data).size == 3
    assert broker.getcash() == 680.0
    assert broker._orders == broker._oidmap == broker._active_orders == {}


def test_resume_preserves_explicit_terminal_target_and_accounts_cancel_fill(pending):
    broker, order, adapter, clock, _ = pending
    adapter.status = {"order_status": "cancelled", "filled_quantity": 2}
    adapter.trades = [trade(1)]
    poll(pending)
    broker.stop()
    clock.now = 100.0
    broker.start()
    adapter.status = {"order_status": "open"}
    adapter.trades = [trade(1), trade(1, 110.0, "fill-2")]
    poll(pending)
    assert notifications(broker) == [(bt.Order.Partial, 1), (bt.Order.Partial, 2),
                                     (bt.Order.Canceled, 2)]
    assert broker.getposition(order.data).size == 2
    assert broker.getcash() == 790.0
    assert order.ref not in broker._oidmap


def test_downtime_and_failed_polls_do_not_confirm_unknown_terminal_settlement(pending):
    broker, order, adapter, clock, _ = pending
    adapter.status = {"order_status": "cancelled"}
    poll(pending)
    broker.stop()
    clock.now = 100.0
    broker.start()
    assert broker._terminal_pending[order.ref].snapshots == 0
    adapter.fail = True
    poll(pending)
    clock.now = 109.0
    poll(pending)
    adapter.fail = False
    clock.now = 110.0
    poll(pending)
    assert order.status == bt.Order.Accepted
    assert broker._terminal_pending[order.ref].snapshots == 1
    clock.now = 120.0
    poll(pending)
    assert notifications(broker) == [(bt.Order.Canceled, 0)]


@pytest.mark.parametrize("damage", ["missing_id", "blank_id", "non_string_id", "orphan_id",
                                    "wrong_ref", "duplicate_id", "orphan_settlement",
                                    "conflicting_active"])
def test_incomplete_recovery_fails_locally_without_changing_state(pending, damage):
    broker, order, adapter, _, launches = pending
    broker.stop()
    if damage == "missing_id":
        broker._oidmap.clear()
    elif damage == "blank_id":
        broker._oidmap[order.ref] = " "
    elif damage == "non_string_id":
        broker._oidmap[order.ref] = 123
    elif damage == "orphan_id":
        broker._oidmap[order.ref + 100] = "orphan"
    elif damage == "wrong_ref":
        broker._orders[order.ref + 100] = broker._orders.pop(order.ref)
    elif damage == "duplicate_id":
        another = OAOrder("BUY", data=Data(), size=1, price=100.0, simulated=True)
        another.accept(broker)
        broker._orders[another.ref] = another
        broker._oidmap[another.ref] = "order-1"
    elif damage == "orphan_settlement":
        broker._terminal_pending[order.ref + 100] = object()
    else:
        broker._active_orders[order.ref] = (order, "other-id")
    with pytest.raises(RuntimeError, match="pending-order recovery"):
        broker.start()
    assert not broker._started
    assert broker._monitor_stop.is_set()
    assert launches == []
    assert adapter.status_ids == []
    assert broker._orders


@pytest.mark.parametrize("status", [bt.Order.Completed, bt.Order.Canceled, bt.Order.Rejected,
                                     bt.Order.Expired])
def test_resume_does_not_monitor_terminal_orders(pending, status):
    broker, order, _, _, launches = pending
    order.status = status
    broker.stop()
    broker.start()
    assert broker._active_orders == {}
    assert launches == []


def test_resume_after_timed_out_worker_exits(pending):
    class OldWorker:
        alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            pass

    broker, order, _, _, launches = pending
    worker = OldWorker()
    broker._monitor_thread = worker
    with pytest.raises(RuntimeError, match="monitor did not stop"):
        broker.stop()
    with pytest.raises(RuntimeError, match="monitor is still stopping"):
        broker.start()
    worker.alive = False
    broker.start()
    assert broker._active_orders == {order.ref: (order, "order-1")}
    assert broker._monitor_thread is None
    assert launches == [True]


def test_resume_creates_real_replacement_worker(monkeypatch):
    broker = OABroker(store=SimpleNamespace(get_client=lambda: None), use_funds=False)
    broker._monitor_interval = 60.0
    polled = threading.Event()
    monkeypatch.setattr(broker, "_reconcile_order", lambda *args: polled.set())
    broker.start()
    order = OAOrder("BUY", data=Data(), size=3, price=100.0, simulated=True)
    order.accept(broker)
    broker._orders[order.ref] = order
    broker._oidmap[order.ref] = "order-1"
    broker._active_orders[order.ref] = (order, "order-1")
    try:
        broker._ensure_monitor_started()
        old = broker._monitor_thread
        assert polled.wait(1.0)
        broker.stop()
        assert not old.is_alive()
        polled.clear()
        broker.start()
        assert polled.wait(1.0)
        new = broker._monitor_thread
        broker.start()
        assert new is not old
        assert new.is_alive()
        assert broker._monitor_thread is new
        broker.stop()
        assert not new.is_alive()
    finally:
        broker.stop()


@pytest.mark.parametrize("raw", [False, True])
def test_resume_multiple_orders_keeps_original_ids(pending, raw):
    broker, order, adapter, _, _ = pending
    if raw:
        broker._adapter = None
        broker._client = adapter
    another = OAOrder("SELL", data=Data(), size=1, price=100.0, simulated=True)
    another.accept(broker)
    broker._orders[another.ref] = another
    broker._oidmap[another.ref] = "order-2"
    broker._active_orders[another.ref] = (another, "order-2")
    broker.stop()
    broker.start()
    assert broker._active_orders == {order.ref: (order, "order-1"),
                                     another.ref: (another, "order-2")}
    for ref, (retained, oid) in list(broker._active_orders.items()):
        broker._reconcile_order(ref, retained, oid)
    assert adapter.status_ids == ["order-1", "order-2"]
    assert notifications(broker) == []


def test_invalid_second_order_cannot_partially_restore_first(pending):
    broker, order, adapter, _, launches = pending
    another = OAOrder("BUY", data=Data(), size=1, price=100.0, simulated=True)
    another.accept(broker)
    broker._orders[another.ref] = another
    broker.stop()
    with pytest.raises(RuntimeError, match="pending-order recovery"):
        broker.start()
    assert broker._active_orders == {}
    assert not broker._started
    assert launches == []
    assert adapter.status_ids == []
    broker._oidmap[another.ref] = "order-2"
    broker.start()
    assert broker._active_orders == {order.ref: (order, "order-1"),
                                     another.ref: (another, "order-2")}
    assert launches == [True]


def test_repeated_resume_keeps_fill_ledger_and_queued_notifications(pending):
    broker, order, adapter, _, _ = pending
    adapter.status["filled_quantity"] = 1
    adapter.trades = [trade(1)]
    poll(pending)
    for _ in range(3):
        broker.stop()
        broker.start()
        poll(pending)
    assert broker._reconciled_fills[order.ref] == 1
    assert broker._reconciled_fill_values[order.ref] == 100.0
    assert broker.getcash() == 900.0
    assert broker.getposition(order.data).size == 1
    assert notifications(broker) == [(bt.Order.Partial, 1)]


def test_cerebro_pending_order_completes_on_same_broker_resume(monkeypatch):
    import pandas as pd

    class PaperTransport(Transport):
        def analyzer(self):
            return SimpleNamespace(data={"mode": "analyze", "analyze_mode": True})

        def place_order(self, **payload):
            self.placements.append(payload)
            return SimpleNamespace(data={"orderid": "order-1"})

    class Strategy(bt.Strategy):
        def __init__(self):
            self.order = None
            self.statuses = []

        def next(self):
            if self.order is None:
                self.order = self.buy(size=3)
                assert polled.wait(1.0)

        def notify_order(self, order):
            self.statuses.append(order.status)

    adapter = PaperTransport()
    adapter.placements = []
    broker = OABroker(store=SimpleNamespace(adapter=adapter, get_client=lambda: None),
                      mode="paper", use_funds=False, cash_fallback=1000.0)
    broker._monitor_interval = 60.0
    polled = threading.Event()
    completed = threading.Event()
    reconcile = broker._reconcile_order

    def observed(ref, order, oid):
        reconcile(ref, order, oid)
        polled.set()
        if order.status == bt.Order.Completed:
            completed.set()

    monkeypatch.setattr(broker, "_reconcile_order", observed)
    monkeypatch.setattr(broker, "reconcile_account", lambda: None)
    candles = pd.DataFrame(
        {"open": [100.0] * 4, "high": [101.0] * 4, "low": [99.0] * 4,
         "close": [100.0] * 4, "volume": [100] * 4},
        index=pd.date_range("2026-08-31 09:15", periods=4, freq="min"),
    )
    cerebro = bt.Cerebro()
    cerebro.setbroker(broker)
    cerebro.adddata(bt.feeds.PandasData(dataname=candles), name="NSE:RELIANCE")
    cerebro.addstrategy(Strategy)
    try:
        strategy = cerebro.run()[0]
        assert strategy.statuses == [bt.Order.Submitted, bt.Order.Accepted]
        assert not broker._started
        assert broker._active_orders == {}
        adapter.status = {"order_status": "complete", "filled_quantity": 3}
        adapter.trades = [trade(3)]
        broker.start()
        assert completed.wait(1.0)
        assert len(adapter.placements) == 1
        assert strategy.order.status == bt.Order.Completed
        assert notifications(broker) == [(bt.Order.Completed, 3)]
        assert broker.getposition(strategy.order.data).size == 3
        assert broker.getcash() == 700.0
        assert broker._oidmap == {}
    finally:
        broker.stop()


def test_inflight_response_from_timed_out_worker_cannot_execute_after_stop(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    class BlockingTransport(Transport):
        def orderstatus(self, **payload):
            entered.set()
            if not release.wait(2.0):
                raise RuntimeError("Offline release signal missing")
            return super().orderstatus(**payload)

    adapter = BlockingTransport()
    adapter.status = {"order_status": "complete", "filled_quantity": 3}
    adapter.trades = [trade(3)]
    broker = OABroker(store=SimpleNamespace(adapter=adapter, get_client=lambda: None),
                      mode="paper", use_funds=False, cash_fallback=1000.0)
    broker._monitor_interval = 60.0
    monkeypatch.setattr(broker, "reconcile_account", lambda: None)
    reconcile = broker._reconcile_order

    def observed(ref, order, oid):
        reconcile(ref, order, oid)
        if order.status == bt.Order.Completed:
            completed.set()

    monkeypatch.setattr(broker, "_reconcile_order", observed)
    broker.start()
    order = OAOrder("BUY", data=Data(), size=3, price=100.0, simulated=True)
    order.accept(broker)
    broker._orders[order.ref] = order
    broker._oidmap[order.ref] = "order-1"
    broker._active_orders[order.ref] = (order, "order-1")
    try:
        broker._ensure_monitor_started()
        old = broker._monitor_thread
        assert entered.wait(1.0)
        join = old.join
        monkeypatch.setattr(old, "join", lambda timeout: join(timeout=0.01))
        with pytest.raises(RuntimeError, match="monitor did not stop"):
            broker.stop()
        with pytest.raises(RuntimeError, match="monitor is still stopping"):
            broker.start()
        release.set()
        join(timeout=1.0)
        assert not old.is_alive()
        assert order.executed.size == 0
        assert broker.getcash() == 1000.0
        assert notifications(broker) == []
        broker.start()
        assert completed.wait(1.0)
        assert broker._monitor_thread is not old
        assert notifications(broker) == [(bt.Order.Completed, 3)]
        assert broker.getcash() == 700.0
    finally:
        release.set()
        broker.stop()
