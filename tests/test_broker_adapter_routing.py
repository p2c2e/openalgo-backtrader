import threading
from types import SimpleNamespace

import backtrader as bt

from openalgo_bt.adapters.openalgo import NormalizedOpenAlgoResponse
from openalgo_bt.brokers.oabroker import OABroker
from openalgo_bt.brokers.rate_limit import BrokerRequestLimiter


class Adapter:
    def __init__(self):
        self.place_calls = []
        self.cancel_calls = []

    def place_order(self, **payload):
        self.place_calls.append(payload)
        return NormalizedOpenAlgoResponse(
            status="success",
            data={"orderid": "order-1"},
            raw={},
            source_key="data",
        )

    def cancel_order(self, **payload):
        self.cancel_calls.append(payload)
        return NormalizedOpenAlgoResponse(status="success", data={}, raw={}, source_key="data")


class Order:
    Market = 0
    Limit = 1
    Stop = 2
    StopLimit = 3

    def __init__(self, exectype=0, price=None, pricelimit=None, size=2):
        self.exectype = exectype
        self.price = price
        self.pricelimit = pricelimit
        self.size = size

    def isbuy(self):
        return True


class Data:
    p = SimpleNamespace(symbol="NSE:RELIANCE", exchange=None)
    _name = "NSE:RELIANCE"


def broker(adapter):
    result = object.__new__(OABroker)
    result._request_limiter = BrokerRequestLimiter(10, 5)
    result._state_lock = threading.RLock()
    result._monitor_wakeup = threading.Event()
    result.p = SimpleNamespace(strategy="test", product="MIS", default_exchange="NSE", debug=False)
    result._adapter = adapter
    result._client = None
    result._oidmap = {7: "order-1"}
    result._poll_stops = {}
    result._orders = {7: object()}
    result._reconciled_fill_values = {}
    result.notify = lambda order: None
    return result


def test_place_order_routes_validated_payload_through_adapter():
    adapter = Adapter()
    result = broker(adapter)

    assert result._place_openalgo_order(Order(), Data(), True) == "order-1"
    assert adapter.place_calls == [
        {
            "strategy": "test",
            "product": "MIS",
            "symbol": "RELIANCE",
            "exchange": "NSE",
            "action": "BUY",
            "quantity": 2,
            "price_type": "MARKET",
        }
    ]


def test_cancel_routes_through_adapter_when_order_id_is_known():
    adapter = Adapter()
    result = broker(adapter)
    order = SimpleNamespace(ref=7, status=bt.Order.Accepted)
    result._orders[order.ref] = order

    result.cancel(order)

    assert adapter.cancel_calls == [{"order_id": "order-1", "strategy": "test"}]
