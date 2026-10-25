from datetime import datetime

import backtrader as bt
import pandas as pd

from openalgo_bt.brokers.oabroker import OABroker


class FakeStore:
    def get_client(self):
        return None


class SingleMarketOrderStrategy(bt.Strategy):
    def __init__(self):
        self.order_statuses = []
        self.trade_count = 0
        self.submitted = False

    def next(self):
        if not self.submitted:
            self.buy(size=2, exectype=bt.Order.Market)
            self.submitted = True

    def notify_order(self, order):
        self.order_statuses.append(order.getstatusname())

    def notify_trade(self, trade):
        if trade.isopen:
            self.trade_count += 1

class RejectedRoutingStrategy(bt.Strategy):
    def __init__(self):
        self.order_statuses = []
        self.submitted = False

    def next(self):
        if not self.submitted:
            self.buy(size=1, exectype=bt.Order.Market)
            self.submitted = True

    def notify_order(self, order):
        self.order_statuses.append(order.getstatusname())


def test_simulated_broker_obeys_cerebro_market_order_lifecycle():
    index = pd.DatetimeIndex(
        [datetime(2026, 8, 31, 9, minute) for minute in range(15, 19)]
    )
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

    cerebro = bt.Cerebro()
    broker = OABroker(store=FakeStore())
    broker.p.simulate_fills = True
    cerebro.setbroker(broker)
    cerebro.adddata(bt.feeds.PandasData(dataname=data), name="NSE:RELIANCE")
    cerebro.addstrategy(SingleMarketOrderStrategy)

    strategy = cerebro.run()[0]

    assert strategy.order_statuses[:3] == ["Submitted", "Accepted", "Completed"]
    assert strategy.trade_count == 1
    assert strategy.getposition().size == 2
    assert cerebro.broker.getcash() == 1_000_000.0 - 2 * 100.5

def test_non_simulated_backtest_mode_rejects_without_acceptance():
    index = pd.DatetimeIndex([datetime(2026, 8, 31, 9, minute) for minute in range(15, 18)])
    data = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.5, 101.5, 102.5],
            "volume": [100, 100, 100],
        },
        index=index,
    )

    cerebro = bt.Cerebro()
    broker = OABroker(store=FakeStore())
    cerebro.setbroker(broker)
    cerebro.adddata(bt.feeds.PandasData(dataname=data), name="NSE:RELIANCE")
    cerebro.addstrategy(RejectedRoutingStrategy)

    strategy = cerebro.run()[0]

    assert strategy.order_statuses == ["Submitted", "Rejected"]
    assert strategy.getposition().size == 0
    assert cerebro.broker.getcash() == 1_000_000.0
