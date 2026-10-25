"""Account snapshots cannot act as execution-ledger watermarks."""

import threading
from types import SimpleNamespace

import backtrader as bt
import pytest

from openalgo_bt.adapters.openalgo import NormalizedOpenAlgoResponse, OpenAlgoResponseError
from openalgo_bt.brokers.account import available_cash
from openalgo_bt.brokers.oabroker import OABroker, OAOrder


def response(data, status="success"):
    return NormalizedOpenAlgoResponse(status=status, data=data, raw={}, source_key="data")


class Transport:
    def __init__(self):
        self.cash = 1000.0
        self.funds_calls = 0
        self.positions = response([])
        self.orders = response({"orders": []})
        self.fail = None
        self.on_funds = None
        self.status = {"order_status": "open", "filled_quantity": 0}
        self.trades = []

    def funds(self):
        self.funds_calls += 1
        if self.on_funds:
            self.on_funds()
        if self.fail == "funds":
            raise RuntimeError("Offline funds unavailable")
        return response({"availablecash": self.cash})

    def positionbook(self):
        if self.fail == "positions":
            raise RuntimeError("Offline positions unavailable")
        return self.positions

    def orderbook(self):
        if self.fail == "orders":
            raise RuntimeError("Offline orders unavailable")
        return self.orders

    def orderstatus(self, **payload):
        return response(dict(self.status))

    def tradebook(self):
        return response(list(self.trades))

    def place_order(self, **payload):
        pytest.fail("Accounting tests must not place orders")

    def cancel_order(self, **payload):
        pytest.fail("Accounting tests must not cancel orders")


class Data:
    close = [100.0]
    p = SimpleNamespace(symbol="RELIANCE", exchange="NSE")


@pytest.fixture
def account(monkeypatch):
    adapter = Transport()
    # Accounting regressions batch endpoint reads without advancing wall time.
    broker = OABroker(store=SimpleNamespace(adapter=adapter, get_client=lambda: None),
                      mode="paper", cash_fallback=1000.0, api_read_rate=1000)
    monkeypatch.setattr(broker, "_ensure_monitor_started", lambda: None)
    broker.start()
    try:
        yield broker, adapter
    finally:
        broker.stop()


def pending(broker, action="BUY", quantity=3):
    order = OAOrder(action, data=Data(), size=quantity, price=100.0, simulated=True)
    order.accept(broker)
    broker._orders[order.ref] = order
    broker._oidmap[order.ref] = "order-1"
    broker._active_orders[order.ref] = (order, "order-1")
    return order


def poll(broker, order, adapter, quantity, price=100.0, status="open"):
    adapter.status = {"order_status": status, "filled_quantity": quantity}
    adapter.trades = [{"orderid": "order-1", "tradeid": "fill-1", "quantity": quantity,
                       "price": price}] if quantity else []
    broker._reconcile_order(order.ref, order, "order-1")


def diagnostics(broker):
    return (broker._external_cash, broker._cash_discrepancy, broker._external_positions,
            broker._external_orders, broker._account_discrepancies, broker._last_account_reconcile)


@pytest.mark.parametrize("action, external, expected", [("BUY", 900.0, 900.0), ("SELL", 1100.0, 1100.0)])
def test_funds_containing_delayed_fill_cannot_double_count_it(account, action, external, expected):
    broker, adapter = account
    order = pending(broker, action)
    adapter.cash = external
    broker.reconcile_account()
    assert broker.getcash() == 1000.0
    assert broker.get_account_cash() == external
    poll(broker, order, adapter, 1)
    assert order.status == bt.Order.Partial
    assert abs(order.executed.size) == 1
    assert broker.getcash() == expected
    broker.reconcile_account()
    assert broker.get_cash_discrepancy() == 0.0
    poll(broker, order, adapter, 1)
    assert broker.getcash() == expected


@pytest.mark.parametrize("external", [1000.0, 900.0, 4500.0, 0.0, -100.0])
def test_stale_reserved_or_unrelated_funds_do_not_undo_local_fill(account, external):
    broker, adapter = account
    order = pending(broker)
    poll(broker, order, adapter, 1)
    adapter.cash = external
    broker.reconcile_account()
    assert broker.getcash() == 900.0
    assert broker.getvalue() == 1000.0
    assert broker.get_account_cash() == external
    assert broker.get_cash_discrepancy() == external - 900.0
    assert broker.startingcash == broker.startingvalue == 1000.0


def test_completed_order_cleanup_does_not_make_funds_safe_to_adopt(account):
    broker, adapter = account
    order = pending(broker)
    poll(broker, order, adapter, 3, status="complete")
    assert broker._orders == broker._active_orders == {}
    assert broker.getcash() == 700.0
    adapter.cash = 1000.0
    broker.reconcile_account()
    broker.stop()
    broker.start()
    assert broker.getcash() == broker.startingcash == 700.0
    assert adapter.funds_calls == 2
    assert broker.get_account_cash() == 1000.0


def test_idle_restart_preserves_fallback_after_initial_funds_failure(monkeypatch):
    adapter = Transport()
    adapter.fail = "funds"
    broker = OABroker(store=SimpleNamespace(adapter=adapter, get_client=lambda: None), cash_fallback=600.0)
    try:
        broker.start()
        broker.stop()
        adapter.fail = None
        broker.start()
        assert broker.getcash() == 600.0
        assert adapter.funds_calls == 1
    finally:
        broker.stop()


def test_use_funds_false_is_respected_by_periodic_account_refresh(account):
    broker, adapter = account
    broker.p.use_funds = False
    adapter.cash = 4500.0
    broker.reconcile_account()
    assert adapter.funds_calls == 1
    assert broker.getcash() == 1000.0
    assert broker.get_account_cash() is None
    assert broker.get_cash_discrepancy() is None
    assert broker._last_account_reconcile > 0


@pytest.mark.parametrize("failure", ["funds", "positions", "orders"])
def test_failed_refresh_preserves_entire_previous_diagnostic_snapshot(account, failure):
    broker, adapter = account
    broker.reconcile_account()
    before = diagnostics(broker)
    adapter.cash = 4500.0
    adapter.positions = response([{"exchange": "NSE", "symbol": "RELIANCE", "quantity": 2}])
    adapter.fail = failure
    with pytest.raises(RuntimeError, match="Offline"):
        broker.reconcile_account()
    assert diagnostics(broker) == before
    assert broker.getcash() == 1000.0


@pytest.mark.parametrize("endpoint", ["funds", "positionbook", "orderbook"])
@pytest.mark.parametrize("envelope", ["normalized", "raw"])
def test_error_envelopes_are_not_successful_account_snapshots(account, monkeypatch, endpoint, envelope):
    broker, adapter = account
    broker.reconcile_account()
    before = diagnostics(broker)
    payload = response({"availablecash": 4500.0}, "error") if envelope == "normalized" else {
        "status": "error", "data": {"availablecash": 4500.0}}
    monkeypatch.setattr(adapter, endpoint, lambda: payload)
    with pytest.raises(OpenAlgoResponseError):
        broker.reconcile_account()
    assert diagnostics(broker) == before


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True, "bad", None])
def test_invalid_funds_cannot_corrupt_startup_or_diagnostics(account, value):
    broker, adapter = account
    broker.reconcile_account()
    before = diagnostics(broker)
    adapter.cash = value
    with pytest.raises(OpenAlgoResponseError):
        broker.reconcile_account()
    assert diagnostics(broker) == before
    fresh = OABroker(store=SimpleNamespace(adapter=adapter, get_client=lambda: None), cash_fallback=600.0)
    try:
        fresh.start()
        assert fresh.getcash() == fresh.startingcash == 600.0
    finally:
        fresh.stop()


def test_preferred_invalid_cash_does_not_fall_through_to_an_unrelated_alias():
    assert available_cash(response({"availablecash": "nan", "net": 4500.0})) is None


def test_raw_funds_response_is_normalized_at_startup():
    client = SimpleNamespace(get_funds=lambda: {"status": "success", "data": {"availablecash": "2500"}})
    broker = OABroker(store=SimpleNamespace(adapter=None, get_client=lambda: client))
    try:
        broker.start()
        assert broker.getcash() == 2500.0
    finally:
        broker.stop()


def test_account_transport_does_not_lock_execution_and_compares_current_local_state(account):
    broker, adapter = account
    order = pending(broker)
    finished = threading.Event()
    errors = []

    def execute():
        try:
            poll(broker, order, adapter, 1)
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    worker = threading.Thread(target=execute)

    def during_funds():
        worker.start()
        assert finished.wait(1.0), "Account transport held the execution state lock"

    adapter.on_funds = during_funds
    try:
        broker.reconcile_account()
    finally:
        worker.join(timeout=2.0)
    assert not worker.is_alive()
    assert not errors
    assert broker.getcash() == 900.0
    assert broker.get_cash_discrepancy() == 100.0
    assert broker._account_discrepancies == {("NSE", "RELIANCE"): (1.0, 0.0)}


@pytest.mark.parametrize("restart", [False, True])
def test_inflight_snapshot_is_discarded_after_shutdown_or_restart(account, restart):
    broker, adapter = account
    broker.reconcile_account()
    before = diagnostics(broker)
    entered = threading.Event()
    release = threading.Event()
    errors = []

    def blocked_funds():
        entered.set()
        assert release.wait(2.0)

    def reconcile():
        try:
            broker.reconcile_account()
        except Exception as exc:
            errors.append(exc)

    adapter.cash = 4500.0
    adapter.on_funds = blocked_funds
    worker = threading.Thread(target=reconcile)
    worker.start()
    try:
        assert entered.wait(1.0)
        broker.stop()
        adapter.on_funds = None
        if restart:
            broker.start()
        release.set()
        worker.join(timeout=2.0)
        assert not worker.is_alive()
        assert not errors
        assert diagnostics(broker) == before
        assert broker.getcash() == 1000.0
    finally:
        release.set()
        worker.join(timeout=2.0)


def test_reconcile_stopped_broker_performs_no_transport(account):
    broker, adapter = account
    broker.stop()
    before = adapter.funds_calls
    broker.reconcile_account()
    assert adapter.funds_calls == before


def test_disabled_funds_at_first_start_remain_diagnostic_only():
    adapter = Transport()
    broker = OABroker(store=SimpleNamespace(adapter=adapter, get_client=lambda: None),
                      use_funds=False, cash_fallback=600.0)
    try:
        broker.start()
        broker.reconcile_account()
        assert adapter.funds_calls == 0
        assert broker.getcash() == 600.0
        assert broker.get_account_cash() is None
        assert broker.get_cash_discrepancy() is None
    finally:
        broker.stop()


def test_setcash_is_an_explicit_adjustment_not_an_external_snapshot(account):
    broker, adapter = account
    broker.setcash(600.0)
    broker.reconcile_account()
    assert broker.getcash() == broker.startingcash == 600.0
    assert broker.get_account_cash() == 1000.0
    assert broker.get_cash_discrepancy() == 400.0


def test_pending_restart_preserves_last_successful_diagnostics(account):
    broker, adapter = account
    pending(broker)
    broker.reconcile_account()
    before = diagnostics(broker)
    broker.stop()
    broker.start()
    assert diagnostics(broker) == before
    assert adapter.funds_calls == 2


def test_same_symbol_across_feeds_is_aggregated_for_comparison(account):
    broker, adapter = account
    broker._positions[Data()] = bt.Position(size=1, price=100.0)
    broker._positions[Data()] = bt.Position(size=2, price=100.0)
    adapter.positions = response([{"exchange": "NSE", "symbol": "RELIANCE", "quantity": 3}])
    broker.reconcile_account()
    assert broker._account_discrepancies == {}


@pytest.mark.parametrize("status", ["error", "failed", "failure", "unknown", False])
@pytest.mark.parametrize("envelope", ["normalized", "raw"])
def test_startup_rejects_failed_funds_and_never_retries_after_restart(status, envelope):
    adapter = Transport()
    payload = response({"availablecash": 4500.0}, status) if envelope == "normalized" else {
        "status": status, "results": {"availablecash": 4500.0}}
    adapter.funds = lambda: payload
    broker = OABroker(store=SimpleNamespace(adapter=adapter, get_client=lambda: None), cash_fallback=600.0)
    try:
        broker.start()
        assert broker.getcash() == 600.0
        assert broker.get_account_cash() is None
        broker.stop()
        adapter.funds = lambda: response({"availablecash": 4500.0})
        broker.start()
        assert broker.getcash() == 600.0
        assert broker.get_account_cash() is None
    finally:
        broker.stop()


def test_successful_raw_account_envelopes_are_normalized(account, monkeypatch):
    broker, adapter = account
    monkeypatch.setattr(adapter, "funds", lambda: '{"status":"success","data":{"availablecash":4500}}')
    monkeypatch.setattr(adapter, "positionbook", lambda: {"status": "ok", "results": [
        {"exchange": "NSE", "symbol": "RELIANCE", "quantity": 2}]})
    monkeypatch.setattr(adapter, "orderbook", lambda: {"status": "success", "data": {"orders": [
        {"orderid": "external-1", "order_status": "open", "exchange": "NSE",
         "symbol": "RELIANCE", "quantity": 2, "action": "BUY"}]}})
    broker.reconcile_account()
    assert broker.getcash() == 1000.0
    assert broker.get_account_cash() == 4500.0
    assert broker._external_positions == {("NSE", "RELIANCE"): 2.0}
    assert "external-1" in broker._external_orders
    assert broker._orders == broker._active_orders == {}
