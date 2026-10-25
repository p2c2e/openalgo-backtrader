from types import SimpleNamespace
from openalgo_bt.adapters.openalgo import NormalizedOpenAlgoResponse
from openalgo_bt.brokers.oabroker import OABroker


class Adapter:
    def funds(self):
        return NormalizedOpenAlgoResponse(status="success", data={"availablecash": "5000"}, raw={}, source_key="data")

    def positionbook(self):
        return NormalizedOpenAlgoResponse(
            status="success",
            data=[{"exchange": "NSE", "symbol": "RELIANCE", "quantity": "2"}],
            raw={},
            source_key="data",
        )

    def orderbook(self):
        return NormalizedOpenAlgoResponse(status="success", data={"orders": []}, raw={}, source_key="data")


def test_reconcile_account_refreshes_diagnostics_without_rebasing_local_cash():
    adapter = Adapter()
    broker = OABroker(store=SimpleNamespace(adapter=adapter, get_client=lambda: None),
                      use_funds=False, cash_fallback=1000.0)
    broker.start()
    broker.p.use_funds = True
    try:
        broker.reconcile_account()

        assert broker.getcash() == broker.getvalue() == 1000.0
        assert broker.startingcash == broker.startingvalue == 1000.0
        assert broker.get_account_cash() == 5000.0
        assert broker.get_cash_discrepancy() == 4000.0
        assert broker._external_positions == {("NSE", "RELIANCE"): 2.0}
        assert broker._external_orders == {}
        assert broker._account_discrepancies == {("NSE", "RELIANCE"): (0.0, 2.0)}
        assert broker._last_account_reconcile > 0
    finally:
        broker.stop()
