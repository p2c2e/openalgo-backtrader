"""Dependency-free adaptation of openalgo_strategies/bt/backtrader_test2.py."""

from datetime import datetime, time

import backtrader as bt
import pandas as pd

from openalgo_bt.brokers.oabroker import OABroker


class FakeStore:
    def get_client(self):
        return None


class TimeBasedStrategy(bt.Strategy):
    """Buy at 09:30 IST and flatten at 15:20 IST, represented as UTC bars."""

    params = (
        ("buy_time", time(4, 0)),
        ("sell_time", time(9, 50)),
    )

    def __init__(self):
        self.order = None
        self.bought_today = False
        self.opened_today = False
        self.last_date = None
        self.statuses = []
        self.completed_sides = []
        self.closed_trades = 0

    def next(self):
        current = bt.num2date(self.data.datetime[0])
        if current.date() != self.last_date:
            self.last_date = current.date()
            self.bought_today = False
            self.opened_today = False

        if self.order:
            return
        if not self.position and not self.bought_today and current.time() >= self.p.buy_time:
            self.order = self.buy(size=1, exectype=bt.Order.Market)
            self.bought_today = True
        elif self.position and self.opened_today and current.time() >= self.p.sell_time:
            self.order = self.sell(size=self.position.size, exectype=bt.Order.Market)

    def notify_order(self, order):
        self.statuses.append(order.getstatusname())
        if order.status == order.Completed:
            self.completed_sides.append("buy" if order.isbuy() else "sell")
            self.opened_today = order.isbuy()
        if order.status in (order.Completed, order.Canceled, order.Rejected, order.Expired):
            self.order = None

    def notify_trade(self, trade):
        if trade.isclosed:
            self.closed_trades += 1


def test_time_based_strategy_completes_deterministic_intraday_round_trip():
    # UTC timestamps correspond to 09:29, 09:30, 09:31, 15:20, and 15:21 IST.
    index = pd.DatetimeIndex(
        [
            datetime(2026, 8, 31, 3, 59),
            datetime(2026, 8, 31, 4, 0),
            datetime(2026, 8, 31, 4, 1),
            datetime(2026, 8, 31, 9, 50),
            datetime(2026, 8, 31, 9, 51),
        ]
    )
    data = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0, 110.0, 111.0],
            "high": [101.0, 102.0, 103.0, 111.0, 112.0],
            "low": [99.0, 100.0, 101.0, 109.0, 110.0],
            "close": [100.5, 101.5, 102.5, 110.5, 111.5],
            "volume": [100, 100, 100, 100, 100],
        },
        index=index,
    )

    cerebro = bt.Cerebro()
    broker = OABroker(store=FakeStore())
    broker.p.simulate_fills = True
    cerebro.setbroker(broker)
    cerebro.adddata(bt.feeds.PandasData(dataname=data), name="NSE:RELIANCE")
    cerebro.addstrategy(TimeBasedStrategy)

    strategy = cerebro.run()[0]

    assert strategy.completed_sides == ["buy", "sell"]
    assert strategy.closed_trades == 1
    assert strategy.getposition().size == 0
    assert strategy.statuses == [
        "Submitted", "Accepted", "Completed",
        "Submitted", "Accepted", "Completed",
    ]
    assert cerebro.broker.getcash() == 1_000_000.0 + 110.5 - 101.5
