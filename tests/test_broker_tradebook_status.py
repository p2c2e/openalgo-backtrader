from openalgo_bt.brokers.oabroker import OABroker
from openalgo_bt.brokers.rate_limit import BrokerRequestLimiter


class TradebookClient:
    def tradebook(self):
        return {
            "data": [
                {"orderid": "order-1", "tradeid": "fill-1", "quantity": 2, "price": 100},
                {"orderid": "order-1", "tradeid": "fill-1", "quantity": 2, "price": 100},
                {"orderid": "order-1", "tradeid": "fill-2", "quantity": 1, "price": 110},
            ]
        }

    def orderstatus(self, **kwargs):
        return {"data": {"order_status": "complete"}}


def test_patched_order_status_uses_deduplicated_tradebook_summary():
    broker = object.__new__(OABroker)
    broker._request_limiter = BrokerRequestLimiter(10, 5)
    broker._client = TradebookClient()

    status = broker.patched_order_status("order-1")

    assert status == {
        "order_status": "complete",
        "filled_quantity": 3,
        "reported_filled_quantity": None,
        "average_price": 103.33333333333333,
        "price": 103.33333333333333,
    }
