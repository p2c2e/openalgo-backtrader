from openalgo_bt.adapters.openalgo import NormalizedOpenAlgoResponse
from openalgo_bt.brokers.oabroker import OABroker


class Adapter:
    def funds(self):
        return NormalizedOpenAlgoResponse(
            status="success",
            data={"availablecash": "4321.50"},
            raw={},
            source_key="data",
        )


class Store:
    def __init__(self):
        self.adapter = Adapter()

    def get_client(self):
        return None


def test_broker_start_uses_adapter_funds_when_available():
    broker = OABroker(store=Store())
    broker.p.use_funds = True
    broker.start()

    assert broker._cash == 4321.5
    assert broker._value == 4321.5
    assert broker.startingcash == 4321.5
    assert broker.startingvalue == 4321.5
