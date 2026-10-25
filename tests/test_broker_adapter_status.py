from openalgo_bt.adapters.openalgo import NormalizedOpenAlgoResponse
from openalgo_bt.brokers.oabroker import OABroker
from openalgo_bt.brokers.rate_limit import BrokerRequestLimiter


class Adapter:
    def __init__(self):
        self.calls = []

    def tradebook(self):
        self.calls.append(("tradebook", {}))
        return NormalizedOpenAlgoResponse(
            status="success",
            data=[{"orderid": "order-1", "tradeid": "fill-1", "quantity": 1, "price": 100}],
            raw={},
            source_key="data",
        )

    def orderstatus(self, **kwargs):
        self.calls.append(("orderstatus", kwargs))
        return NormalizedOpenAlgoResponse(
            status="success",
            data={"order_status": "complete"},
            raw={},
            source_key="data",
        )


def test_patched_order_status_uses_adapter_boundary_when_available():
    broker = object.__new__(OABroker)
    broker._request_limiter = BrokerRequestLimiter(10, 5)
    broker._adapter = Adapter()
    broker._client = None

    assert broker.patched_order_status("order-1") == {
        "order_status": "complete",
        "filled_quantity": 1,
        "reported_filled_quantity": None,
        "average_price": 100.0,
        "price": 100.0,
    }
    assert broker._adapter.calls == [
        ("orderstatus", {"order_id": "order-1", "strategy": "BackTrader Polling"}),
        ("tradebook", {}),
    ]
