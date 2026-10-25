import threading
from types import SimpleNamespace

import backtrader as bt
import pandas as pd
import pytest

from openalgo_bt.brokers.oabroker import OABroker


class FundsAdapter:
    def __init__(self, cash=2500.0, fail=False):
        self.cash = cash
        self.fail = fail
        self.calls = 0

    def funds(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Offline funds failure")
        return SimpleNamespace(data={"availablecash": self.cash})


class Store:
    def __init__(self, adapter=None, client=None):
        self.adapter = adapter
        self.client = client

    def get_client(self):
        return self.client


def test_start_initializes_funds_once_and_remains_started(monkeypatch):
    adapter = FundsAdapter()
    broker = OABroker(store=Store(adapter=adapter))
    parent_stops = []
    monkeypatch.setattr(bt.BrokerBase, "stop", lambda self: parent_stops.append(self))
    broker._monitor_stop.set()
    broker._monitor_wakeup.set()
    pending = object()
    broker._active_orders[1] = (pending, "order-1")

    try:
        broker.start()
        broker.start()

        assert broker._started
        assert not broker._monitor_stop.is_set()
        assert not broker._monitor_wakeup.is_set()
        assert broker._monitor_thread is None
        assert broker._active_orders == {1: (pending, "order-1")}
        assert adapter.calls == 1
        assert broker.getcash() == broker.getvalue() == 2500.0
        assert broker.startingcash == broker.startingvalue == 2500.0
        assert parent_stops == []
    finally:
        broker.stop()


@pytest.mark.parametrize("fail, use_funds", [(True, True), (False, False)])
def test_start_keeps_cash_fallback_when_funds_unavailable_or_disabled(fail, use_funds):
    adapter = FundsAdapter(fail=fail)
    broker = OABroker(store=Store(adapter=adapter), cash_fallback=1234.0, use_funds=use_funds)
    try:
        broker.start()

        assert broker._started
        assert adapter.calls == int(use_funds)
        assert broker.getcash() == broker.startingcash == 1234.0
        assert broker.getvalue() == broker.startingvalue == 1234.0
    finally:
        broker.stop()


def test_start_preserves_raw_client_funds_fallback():
    client = SimpleNamespace(get_funds=lambda: {"availablecash": "3500"})
    broker = OABroker(store=Store(client=client))
    try:
        broker.start()
        assert broker._started
        assert broker.getcash() == broker.startingcash == 3500.0
    finally:
        broker.stop()


def test_stop_wakes_and_joins_monitor_without_cancelling_orders(monkeypatch):
    broker = OABroker(store=Store(), use_funds=False)
    broker._monitor_interval = 60.0
    reconciled = threading.Event()
    pending = object()
    parent_stops = []
    monkeypatch.setattr(bt.BrokerBase, "stop", lambda self: parent_stops.append(self))
    broker._reconcile_order = lambda *args: reconciled.set()
    broker.start()
    broker._active_orders[1] = (pending, "order-1")
    broker._orders[1] = pending
    broker._oidmap[1] = "order-1"
    monitor = None
    try:
        broker._ensure_monitor_started()
        monitor = broker._monitor_thread
        assert reconciled.wait(1.0)
        broker._ensure_monitor_started()
        assert broker._monitor_thread is monitor

        broker.stop()
        broker.stop()
        broker._ensure_monitor_started()

        assert not monitor.is_alive()
        assert not broker._started
        assert broker._monitor_stop.is_set()
        assert broker._monitor_thread is None
        assert broker._active_orders == {}
        assert broker._orders == {1: pending}
        assert broker._oidmap == {1: "order-1"}
        assert parent_stops == [broker]
    finally:
        broker._monitor_stop.set()
        broker._monitor_wakeup.set()
        if monitor is not None:
            monitor.join(timeout=1.0)
        broker.stop()


def test_broker_can_start_a_fresh_monitor_after_clean_stop():
    broker = OABroker(store=Store(), use_funds=False)
    broker._monitor_interval = 60.0
    old_monitor = None
    new_monitor = None
    try:
        broker.start()
        broker._ensure_monitor_started()
        old_monitor = broker._monitor_thread
        broker.stop()
        assert not old_monitor.is_alive()

        broker.start()
        assert broker._started
        assert not broker._monitor_stop.is_set()
        broker._ensure_monitor_started()
        new_monitor = broker._monitor_thread
        assert new_monitor is not old_monitor
        assert new_monitor.is_alive()
        broker.stop()
        assert not new_monitor.is_alive()
    finally:
        broker._monitor_stop.set()
        broker._monitor_wakeup.set()
        for monitor in (old_monitor, new_monitor):
            if monitor is not None:
                monitor.join(timeout=1.0)
        broker.stop()


def test_stop_before_start_is_safe():
    broker = OABroker(store=Store(), use_funds=False)
    broker.stop()
    broker.stop()
    assert not broker._started
    assert broker._monitor_thread is None
    assert broker._monitor_stop.is_set()


def test_timed_out_shutdown_retains_monitor_and_blocks_restart():
    class BlockedMonitor:
        def __init__(self):
            self.alive = True
            self.join_timeouts = []

        def join(self, timeout):
            self.join_timeouts.append(timeout)

        def is_alive(self):
            return self.alive

    broker = OABroker(store=Store(), use_funds=False)
    broker.start()
    monitor = BlockedMonitor()
    broker._monitor_thread = monitor
    pending = object()
    broker._active_orders[1] = (pending, "order-1")
    try:
        with pytest.raises(RuntimeError, match="monitor did not stop"):
            broker.stop()

        assert monitor.join_timeouts == [2.0]
        assert broker._monitor_thread is monitor
        assert broker._monitor_stop.is_set()
        assert not broker._started
        assert broker._active_orders == {1: (pending, "order-1")}
        with pytest.raises(RuntimeError, match="monitor is still stopping"):
            broker.start()
        broker._ensure_monitor_started()
        assert broker._monitor_thread is monitor
        assert broker._monitor_stop.is_set()

        monitor.alive = False
        broker.stop()
        assert broker._monitor_thread is None
        assert broker._active_orders == {}
        broker.start()
        assert broker._started
    finally:
        monitor.alive = False
        broker.stop()


def test_monitor_stops_between_orders_and_skips_account_refresh():
    adapter = FundsAdapter()
    broker = OABroker(store=Store(adapter=adapter), use_funds=False)
    broker.start()
    broker._active_orders = {1: (object(), "order-1"), 2: (object(), "order-2")}
    reconciled = []

    def request_stop(ref, order, order_id):
        reconciled.append(ref)
        broker._monitor_stop.set()
        broker._monitor_wakeup.set()

    broker._reconcile_order = request_stop
    try:
        broker._monitor_orders()
        assert reconciled == [1]
        assert adapter.calls == 0
    finally:
        broker.stop()


def test_cerebro_stops_monitor_with_an_upstream_order_still_open():
    class PaperAdapter:
        def __init__(self):
            self.placements = []

        def analyzer(self):
            return SimpleNamespace(data={"mode": "analyze", "analyze_mode": True})

        def place_order(self, **payload):
            self.placements.append(payload)
            return SimpleNamespace(data={"orderid": "paper-order-1"})

    class PendingOrderStrategy(bt.Strategy):
        def __init__(self):
            self.order = None
            self.monitor = None
            self.statuses = []

        def start(self):
            assert self.broker._started

        def next(self):
            assert self.broker._started
            if self.order is None:
                self.order = self.buy(size=1)
                self.monitor = self.broker._monitor_thread
                assert polled.wait(1.0)

        def notify_order(self, order):
            self.statuses.append(order.getstatusname())

    adapter = PaperAdapter()
    broker = OABroker(store=Store(adapter=adapter), mode="paper", use_funds=False)
    broker._monitor_interval = 60.0
    polled = threading.Event()

    def pending_status(orderid):
        polled.set()
        return {"order_status": "open", "filled_quantity": 0}

    broker.patched_order_status = pending_status
    broker.reconcile_account = lambda: None
    candles = pd.DataFrame(
        {"open": [100.0] * 4, "high": [101.0] * 4, "low": [99.0] * 4,
         "close": [100.0] * 4, "volume": [100] * 4},
        index=pd.date_range("2026-08-31 09:15", periods=4, freq="min"),
    )
    cerebro = bt.Cerebro()
    cerebro.setbroker(broker)
    cerebro.adddata(bt.feeds.PandasData(dataname=candles), name="NSE:RELIANCE")
    cerebro.addstrategy(PendingOrderStrategy)
    try:
        strategy = cerebro.run()[0]

        assert strategy.statuses == ["Submitted", "Accepted"]
        assert strategy.order.status == bt.Order.Accepted
        assert len(adapter.placements) == 1
        assert not strategy.monitor.is_alive()
        assert broker._monitor_thread is None
        assert broker._monitor_stop.is_set()
        assert not broker._started
        assert broker._active_orders == {}
        assert broker._oidmap[strategy.order.ref] == "paper-order-1"
    finally:
        broker.stop()
