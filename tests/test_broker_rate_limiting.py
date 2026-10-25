"""Deterministic broker request admission, fairness and no write retries."""

import importlib
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import backtrader as bt
import pytest

from openalgo_bt.brokers.oabroker import OABroker, OAOrder


def limiter_types():
    module = importlib.import_module("openalgo_bt.brokers.rate_limit")
    return module.BrokerRequestLimiter, module.BrokerRequestDeferred


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.mark.parametrize("read,write", [(0, 5), (-1, 5), (True, 5), (1.5, 5),
                                        ("10", 5), (None, 5), (float("inf"), 5),
                                        (10, 0), (10, -1), (10, True), (10, 2.5),
                                        (10, "5"), (10, None), (10, float("nan"))])
def test_invalid_budget_fails_locally(read, write):
    with pytest.raises(ValueError):
        OABroker(store=SimpleNamespace(get_client=lambda: None),
                 api_read_rate=read, api_write_rate=write)


def test_rolling_window_boundary_and_independent_write_budget():
    Limiter, Deferred = limiter_types()
    clock = Clock()
    limiter = Limiter(2, 1, clock=clock)
    limiter.acquire()
    clock.now = 0.25
    limiter.acquire()
    limiter.acquire(write=True)
    with pytest.raises(Deferred) as caught:
        limiter.acquire()
    assert caught.value.retry_after == pytest.approx(0.75)
    with pytest.raises(Deferred):
        limiter.acquire(write=True)
    clock.now = 1.0
    limiter.acquire()
    with pytest.raises(Deferred):
        limiter.acquire()
    clock.now = 1.25
    limiter.acquire()
    limiter.acquire(write=True)


def test_cooldown_applies_to_both_budgets_and_never_shortens():
    Limiter, Deferred = limiter_types()
    clock = Clock()
    limiter = Limiter(10, 5, clock=clock)
    limiter.defer(20)
    clock.now = 2
    limiter.defer(1)
    for write in (False, True):
        with pytest.raises(Deferred) as caught:
            limiter.acquire(write=write)
        assert caught.value.retry_after == 18
    clock.now = 20
    limiter.acquire()
    limiter.acquire(write=True)


def test_admission_is_atomic_across_threads():
    Limiter, Deferred = limiter_types()
    limiter = Limiter(7, 3, clock=Clock())
    def admit(index):
        try:
            limiter.acquire(write=index % 2 == 0)
            return index % 2 == 0
        except Deferred:
            return None
    with ThreadPoolExecutor(max_workers=12) as executor:
        admitted = list(executor.map(admit, range(100)))
    assert admitted.count(False) == 7
    assert admitted.count(True) == 3


class Transport:
    def __init__(self):
        self.calls = []
        self.fail = None
        self.trades = []

    def read(self, endpoint):
        self.calls.append(endpoint)
        if self.fail:
            raise self.fail
        return SimpleNamespace(data={})

    def funds(self):
        self.read("funds")
        return {"availablecash": 1000.0}

    def positionbook(self):
        self.read("positions")
        return []

    def orderbook(self):
        self.read("orders")
        return []

    def analyzer(self):
        self.read("analyzer")
        return SimpleNamespace(data={"mode": "paper", "analyze_mode": True})

    def orderstatus(self, **payload):
        self.read("status:" + payload["order_id"])
        return SimpleNamespace(data={"order_status": "open"})

    def tradebook(self):
        self.read("trades")
        return SimpleNamespace(data=self.trades)

    def place_order(self, **payload):
        self.read("place")
        return {"status": "success", "orderid": "placed-1"}

    def cancel_order(self, **payload):
        self.read("cancel")
        return {"status": "success"}

    placeorder = place_order
    cancelorder = cancel_order
    get_funds = funds


class Data:
    _name = "SBIN"
    close = [100.0]
    p = SimpleNamespace(symbol="SBIN", exchange="NSE")

    def __len__(self):
        return 1


@pytest.fixture
def setup(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("openalgo_bt.brokers.oabroker.time.monotonic", clock)
    transport = Transport()
    broker = OABroker(store=SimpleNamespace(adapter=transport, get_client=lambda: transport),
                      mode="paper", use_funds=False, cash_fallback=1000,
                      api_read_rate=4, api_write_rate=1)
    broker.start()
    monkeypatch.setattr(broker, "_ensure_monitor_started", lambda: None)
    try:
        yield broker, transport, clock
    finally:
        broker.stop()


def track(broker, upstream_id="pending-1"):
    order = OAOrder("BUY", data=Data(), size=3, price=100.0, simulated=True)
    order.accept(broker)
    broker._orders[order.ref] = order
    broker._oidmap[order.ref] = upstream_id
    broker._active_orders[order.ref] = (order, upstream_id)
    return order


@pytest.mark.parametrize("raw", [False, True])
def test_status_tradebook_pair_uses_shared_budget(setup, raw):
    broker, transport, _ = setup
    if raw:
        broker._adapter = None
    broker.patched_order_status("pending-1")
    broker.patched_order_status("pending-2")
    _, Deferred = limiter_types()
    with pytest.raises(Deferred):
        broker.patched_order_status("pending-3")
    assert transport.calls == ["status:pending-1", "trades", "status:pending-2", "trades"]


def test_partial_pair_deferral_does_not_apply_fills_or_terminal_state(setup):
    broker, transport, _ = setup
    order = track(broker)
    transport.trades = [{"orderid": "pending-1", "quantity": 3, "price": 100.0}]
    for _ in range(3):
        broker._analyzer_mode()
    _, Deferred = limiter_types()
    with pytest.raises(Deferred):
        broker._reconcile_order(order.ref, order, "pending-1")
    assert transport.calls == ["analyzer"] * 3 + ["status:pending-1"]
    assert order.executed.size == 0
    assert broker.getcash() == 1000
    assert order.ref in broker._active_orders
    assert broker.get_notification() is None


def test_account_refresh_deferral_keeps_previous_complete_diagnostics(setup):
    broker, transport, clock = setup
    broker.reconcile_account()
    assert transport.calls == ["positions", "orders"]
    baseline = broker._last_account_reconcile
    broker._external_positions = {("NSE", "old"): 2}
    broker._analyzer_mode()
    clock.now = 0.5
    _, Deferred = limiter_types()
    with pytest.raises(Deferred):
        broker.reconcile_account()
    assert transport.calls[-1] == "positions"
    assert broker._external_positions == {("NSE", "old"): 2}
    assert broker._last_account_reconcile == baseline
    assert broker.getcash() == 1000


def test_analyzer_budget_exhaustion_rejects_before_placement(setup):
    broker, transport, _ = setup
    for _ in range(4):
        broker._analyzer_mode()
    order = broker.buy(None, Data(), 1, simulated=True)
    assert order.status == bt.Order.Rejected
    assert transport.calls == ["analyzer"] * 4
    assert order.ref not in broker._orders


@pytest.mark.parametrize("raw", [False, True])
def test_place_and_cancel_share_write_budget_without_retries(setup, raw):
    broker, transport, clock = setup
    if raw:
        # The production mode guard requires an adapter; only test raw placement fallback.
        broker.validate_routing_mode = lambda: None
        broker._adapter = None
    order = broker.buy(None, Data(), 1, simulated=True)
    assert order.status == bt.Order.Accepted
    broker.cancel(order)
    assert "cancel" not in transport.calls
    assert order.ref in broker._active_orders
    second = broker.buy(None, Data(), 1, simulated=True)
    assert second.status == bt.Order.Rejected
    assert transport.calls.count("place") == 1
    clock.now = 1
    broker.cancel(order)
    assert transport.calls.count("cancel") == 1
    assert order.status == bt.Order.Accepted
    assert order.ref in broker._active_orders


def test_transport_failure_consumes_slot_and_never_retries(setup):
    broker, transport, _ = setup
    transport.fail = RuntimeError("Offline write failure")
    broker.validate_routing_mode = lambda: None
    order = broker.buy(None, Data(), 1, simulated=True)
    transport.fail = None
    second = broker.buy(None, Data(), 1, simulated=True)
    assert order.status == second.status == bt.Order.Rejected
    assert transport.calls == ["place"]


def test_server_throttle_cools_all_requests_and_preserves_cancel_state(setup):
    from openalgo_bt.adapters import openalgo as adapter_module
    broker, transport, clock = setup
    order = track(broker)
    transport.fail = adapter_module.OpenAlgoRateLimitError(retry_after=15)
    broker.cancel(order)
    transport.fail = None
    _, Deferred = limiter_types()
    with pytest.raises(Deferred):
        broker._analyzer_mode()
    clock.now = 14
    broker.cancel(order)
    assert transport.calls == ["cancel"]
    assert order.status == bt.Order.Accepted
    clock.now = 15
    broker._analyzer_mode()
    broker.cancel(order)
    assert transport.calls == ["cancel", "analyzer", "cancel"]


def test_stop_start_does_not_reset_admission_or_cooldown(setup):
    broker, transport, clock = setup
    for _ in range(4):
        broker._analyzer_mode()
    broker.stop()
    broker.start()
    _, Deferred = limiter_types()
    with pytest.raises(Deferred):
        broker._analyzer_mode()
    broker._request_limiter.defer(10)
    clock.now = 2
    broker.stop()
    broker.start()
    with pytest.raises(Deferred):
        broker._analyzer_mode()
    assert transport.calls == ["analyzer"] * 4
    clock.now = 12
    broker._analyzer_mode()


def test_monitor_rotates_orders_when_read_budget_is_exhausted(setup):
    broker, transport, clock = setup
    for oid in ("a", "b", "c", "d", "e"):
        track(broker, oid)
    class Wakeup:
        def __init__(self):
            self.cycles = 0
        def wait(self, timeout):
            self.cycles += 1
            clock.now += timeout
            if self.cycles == 3:
                broker._monitor_stop.set()
        def clear(self):
            pass
        def set(self):
            pass
    broker._monitor_wakeup = Wakeup()
    broker._account_reconcile_not_before = 1000
    broker._monitor_orders()
    status_calls = [call for call in transport.calls if call.startswith("status:")]
    assert status_calls == ["status:a", "status:b", "status:c", "status:d", "status:e", "status:a"]
    assert len(broker._active_orders) == 5
    assert broker.getcash() == 1000


def test_transport_does_not_hold_state_or_limiter_locks(setup):
    broker, transport, _ = setup
    def check():
        acquired = threading.Event()
        def inspect():
            with broker._state_lock:
                broker._request_limiter.acquire(write=True)
                acquired.set()
        thread = threading.Thread(target=inspect)
        thread.start()
        assert acquired.wait(1)
        thread.join(1)
        return SimpleNamespace(data={"mode": "paper", "analyze_mode": True})
    transport.analyzer = check
    broker._analyzer_mode()


def test_simulated_orders_consume_no_request_budget(setup):
    broker, transport, _ = setup
    broker.p.simulate_fills = True
    for _ in range(12):
        order = broker.buy(None, Data(), 1, simulated=True)
        assert order.status == bt.Order.Completed
    assert transport.calls == []
    for _ in range(4):
        broker._analyzer_mode()
