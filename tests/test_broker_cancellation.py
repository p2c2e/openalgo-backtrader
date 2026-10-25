from datetime import datetime

import backtrader as bt
import pandas as pd
import pytest

from openalgo_bt.brokers.oabroker import OABroker


class PaperAdapter:
    def __init__(self):
        self.cancelled = []
        self.status = "OPEN"


    def analyzer(self):
        return type("Response", (), {"data": {"mode": "analyze", "analyze_mode": True}})()

    def place_order(self, **kwargs):
        return type("Response", (), {"data": {"orderid": "paper-order-1"}})()

    def cancel_order(self, **kwargs):
        self.cancelled.append(kwargs)
        return type("Response", (), {"data": {}})()

    def tradebook(self):
        return type("Response", (), {"data": []})()

    def orderstatus(self, **kwargs):
        return type("Response", (), {"data": {"order_status": self.status, "filled_quantity": 0}})()

class PendingClient:
    def __init__(self):
        self.cancelled = []

    def placeorder(self, **kwargs):
        return {"orderid": "paper-order-1"}

    def tradebook(self):
        return {"data": []}

    def orderstatus(self, **kwargs):
        return {"data": {"order_status": "OPEN"}}

    def cancelorder(self, **kwargs):
        self.cancelled.append(kwargs)


class PaperStore:
    def __init__(self):
        self.client = PendingClient()
        self.adapter = PaperAdapter()

    def get_client(self):
        return self.client


class CancelPendingOrderStrategy(bt.Strategy):
    params = (("confirm_cancel", True),)

    def __init__(self):
        self.order = None
        self.order_statuses = []
        self.cancelled = False

    def next(self):
        if self.order is None:
            self.order = self.buy(size=1, exectype=bt.Order.Market)
        elif not self.cancelled:
            self.cancel(self.order)
            assert self.order.status == bt.Order.Accepted
            assert self.order.ref in self.broker._active_orders
            if self.p.confirm_cancel:
                self.broker._adapter.status = "CANCELLED"
            self.broker._reconcile_order(
                self.order.ref, self.order, self.broker._oidmap[self.order.ref],
            )
            self.cancelled = True

    def notify_order(self, order):
        self.order_statuses.append(order.getstatusname())


@pytest.mark.parametrize("confirm_cancel", [False, True])
def test_paper_order_waits_for_cancellation_confirmation(monkeypatch, confirm_cancel):
    index = pd.DatetimeIndex([datetime(2026, 8, 31, 9, minute) for minute in range(15, 19)])
    data = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0, 103.0],
            "high": [101.0, 102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0, 102.0],
            "close": [100.5, 101.5, 102.5, 103.5],
            "volume": [100, 100, 100, 100],
        },
        index=index,
    )
    store = PaperStore()
    cerebro = bt.Cerebro()
    broker = OABroker(store=store, mode="paper", use_funds=False)
    # Drive fake status polls synchronously so the historical replay cannot outrun a worker.
    monkeypatch.setattr(broker, "_ensure_monitor_started", lambda: None)
    cerebro.setbroker(broker)
    cerebro.adddata(bt.feeds.PandasData(dataname=data), name="NSE:RELIANCE")
    cerebro.addstrategy(CancelPendingOrderStrategy, confirm_cancel=confirm_cancel)

    strategy = cerebro.run()[0]

    expected = ["Submitted", "Accepted"]
    if confirm_cancel:
        expected.append("Canceled")
    assert strategy.order_statuses == expected
    assert (strategy.order.ref in broker._orders) is not confirm_cancel
    assert (strategy.order.ref in broker._oidmap) is not confirm_cancel
    assert broker._active_orders == {}
    assert store.adapter.cancelled == [{"order_id": "paper-order-1", "strategy": "Backtrader"}]
    assert broker._poll_threads == {}
    assert broker._poll_stops == {}
