import backtrader as bt

from openalgo_bt.brokers.oabroker import OABroker


class FakeData:
    def __init__(self):
        self.close = [100.0]


class FakeOrder:
    def __init__(self, size=3, buy=True):
        self.size = size
        self.data = FakeData()
        self.comminfo = None
        self._buy = buy
        self.executions = []
        self.status = None

    def isbuy(self):
        return self._buy

    def execute(self, *args):
        self.executions.append(args)

    def partial(self):
        self.status = "partial"

    def completed(self):
        self.status = "completed"


def make_broker():
    broker = object.__new__(OABroker)
    broker._reconciled_fills = {}
    broker._reconciled_fill_values = {}
    broker._positions = {}
    broker._cash = 1_000.0
    broker._notifs = []
    broker.notify = lambda order: broker._notifs.append(order.status)
    return broker


def test_execution_delta_emits_partial_then_only_applies_new_quantity():
    broker = make_broker()
    order = FakeOrder(size=3)

    assert broker._apply_execution_delta(1, order, 1, 100.0, terminal=False)
    assert order.status == "partial"
    assert broker._cash == 900.0
    assert broker._positions[order.data].size == 1

    assert not broker._apply_execution_delta(1, order, 1, 100.0, terminal=False)
    assert broker._cash == 900.0
    assert len(order.executions) == 1

    # Aggregate tradebook price is 106.666..., so the two new units are valued at 110.
    assert broker._apply_execution_delta(1, order, 3, 106.66666666666667, terminal=True)
    assert order.status == "completed"
    assert broker._cash == 680.0
    assert broker._positions[order.data].size == 3
    assert [args[1] for args in order.executions] == [1, 2]
    assert [args[2] for args in order.executions] == [100.0, 110.0]
    assert broker._notifs == ["partial", "completed"]


def test_execution_delta_caps_aggregate_quantity_at_requested_order_size():
    broker = make_broker()
    order = FakeOrder(size=2)

    assert broker._apply_execution_delta(1, order, 10, 100.0, terminal=True)
    assert broker._reconciled_fills[1] == 2
    assert broker._positions[order.data].size == 2
    assert order.status == "completed"
