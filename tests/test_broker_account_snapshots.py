"""Strict account evidence must not replace earlier successful diagnostics."""

import json
from types import SimpleNamespace

import pytest

from openalgo_bt.adapters.openalgo import NormalizedOpenAlgoResponse, OpenAlgoResponseError
from openalgo_bt.brokers.account import orders_by_id, positions_by_symbol
from openalgo_bt.brokers.oabroker import OABroker


def response(data, status="success"):
    return NormalizedOpenAlgoResponse(status=status, data=data, raw={}, source_key="data")


def position(**changes):
    return {"exchange": "NSE", "symbol": "RELIANCE", "quantity": "2", **changes}


def order(**changes):
    return {"orderid": "external-1", "exchange": "NSE", "symbol": "RELIANCE",
            "action": "BUY", "quantity": "2", "order_status": "open", **changes}


PROJECTIONS = [positions_by_symbol, orders_by_id]
BAD_CONTAINERS = [None, {}, {"unexpected": []}, "not-json", 1, True,
                  {"positions": None}, {"orders": None}, {"positions": {}}, {"orders": {}},
                  {"positions": "[]"}, {"orders": "[]"}, (), iter(())]


@pytest.mark.parametrize("projection", PROJECTIONS)
@pytest.mark.parametrize("payload", BAD_CONTAINERS)
def test_invalid_container_is_not_an_empty_book(projection, payload):
    with pytest.raises(OpenAlgoResponseError):
        projection(response(payload))


@pytest.mark.parametrize("projection, key, row", [
    (positions_by_symbol, "positions", position()), (orders_by_id, "orders", order())])
@pytest.mark.parametrize("shape", ["list", "container", "data", "results", "json", "normalized"])
def test_documented_containers_and_envelopes(projection, key, row, shape):
    payload = [row]
    if shape == "container":
        payload = {key: payload, "statistics": {"total": 1}}
    elif shape in ("data", "results"):
        payload = {"status": "success", shape: payload}
    elif shape == "json":
        payload = json.dumps({"status": "ok", "data": {key: payload}})
    elif shape == "normalized":
        payload = response(payload)
    result = projection(payload)
    assert result == ({("NSE", "RELIANCE"): 2.0} if key == "positions" else {"external-1": row})


@pytest.mark.parametrize("projection, key", [(positions_by_symbol, "positions"), (orders_by_id, "orders")])
def test_explicit_empty_books_and_failed_envelopes(projection, key):
    for payload in ([], {key: []}, response([]), {"status": "success", "data": {key: []}}):
        assert projection(payload) == {}
    for payload in (response([], "error"), {"status": "failed", "data": []},
                    '{"status":"error","results":[]}'):
        with pytest.raises(OpenAlgoResponseError):
            projection(payload)


BAD_TEXT = [None, "", "  ", 123, True, [], {}]
BAD_QUANTITY = [None, "", "bad", True, False, float("nan"), float("inf"),
                float("-inf"), "NaN", "Infinity", "1e999", [], {}, b"2"]


@pytest.mark.parametrize("field", ["exchange", "symbol"])
@pytest.mark.parametrize("bad", BAD_TEXT)
def test_invalid_position_identity(field, bad):
    with pytest.raises(OpenAlgoResponseError):
        positions_by_symbol([position(), position(**{field: bad})])


@pytest.mark.parametrize("bad", BAD_QUANTITY)
def test_invalid_position_quantity(bad):
    with pytest.raises(OpenAlgoResponseError):
        positions_by_symbol([position(), position(quantity=bad)])


@pytest.mark.parametrize("field", ["exchange", "symbol", "quantity"])
def test_missing_position_field(field):
    row = position()
    del row[field]
    with pytest.raises(OpenAlgoResponseError):
        positions_by_symbol([row])


@pytest.mark.parametrize("projection, valid", [(positions_by_symbol, position()), (orders_by_id, order())])
@pytest.mark.parametrize("bad", [None, 1, "row", [], True])
def test_any_malformed_row_rejects_the_whole_book(projection, valid, bad):
    with pytest.raises(OpenAlgoResponseError):
        projection([valid, bad])


def test_positions_support_aliases_products_zero_and_short_quantities():
    assert positions_by_symbol([
        position(product="MIS"), position(quantity=-1, product="CNC"),
        {"exchange": "NFO", "tradingsymbol": "OPTION", "netqty": "-3"},
        {"exchange": "BSE", "symbol": "ZERO", "net_quantity": 0},
        position(symbol="ALIASES", tradingsymbol="ALIASES", quantity="2", netqty=2, net_quantity=2.0),
    ]) == {("NSE", "RELIANCE"): 1.0, ("NFO", "OPTION"): -3.0,
           ("BSE", "ZERO"): 0.0, ("NSE", "ALIASES"): 2.0}


def test_position_aggregate_overflow_rejects_snapshot():
    with pytest.raises(OpenAlgoResponseError):
        positions_by_symbol([position(quantity=1e308), position(quantity=1e308)])


@pytest.mark.parametrize("changes", [
    {"tradingsymbol": "DIFFERENT"}, {"tradingsymbol": None}, {"netqty": 3},
    {"netqty": True}, {"net_quantity": float("nan")}, {"net_quantity": None}])
def test_position_alias_cannot_hide_invalid_or_conflicting_evidence(changes):
    with pytest.raises(OpenAlgoResponseError):
        positions_by_symbol([position(**changes)])


@pytest.mark.parametrize("field", ["exchange", "symbol", "action", "order_status"])
@pytest.mark.parametrize("bad", BAD_TEXT)
def test_invalid_order_identity_and_state(field, bad):
    with pytest.raises(OpenAlgoResponseError):
        orders_by_id([order(), order(orderid="external-2", **{field: bad})])


@pytest.mark.parametrize("bad", BAD_QUANTITY + [-1, "-2"])
def test_invalid_order_requested_quantity(bad):
    with pytest.raises(OpenAlgoResponseError):
        orders_by_id([order(quantity=bad)])


@pytest.mark.parametrize("bad", [None, "", "  ", True, False, 1.5, 1.0, -1, [], {}])
def test_invalid_order_id(bad):
    with pytest.raises(OpenAlgoResponseError):
        orders_by_id([order(orderid=bad)])


@pytest.mark.parametrize("field", ["exchange", "symbol", "action", "order_status", "orderid", "quantity"])
def test_missing_order_field(field):
    row = order()
    del row[field]
    with pytest.raises(OpenAlgoResponseError):
        orders_by_id([row])


@pytest.mark.parametrize("changes", [
    {"order_id": "different"}, {"id": None}, {"order_id": True}, {"tradingsymbol": "different"},
    {"tradingsymbol": " "}, {"status": "complete"}, {"status": None}, {"action": "HOLD"}])
def test_order_alias_cannot_hide_invalid_or_conflicting_evidence(changes):
    with pytest.raises(OpenAlgoResponseError):
        orders_by_id([order(**changes)])


def test_order_aliases_integer_ids_unknown_status_and_metadata_are_retained():
    row = {"order_id": 0, "id": "0", "exchange": "BSE", "tradingsymbol": "OTHER",
           "quantity": 0, "action": "sell", "status": "backend-new-state", "metadata": {"tag": "test"}}
    assert orders_by_id([row]) == {"0": row}
    assert orders_by_id([order(), dict(order())]) == {"external-1": order()}


@pytest.mark.parametrize("changes", [{"quantity": 3}, {"order_status": "complete"},
                                     {"exchange": "BSE"}, {"product": "MIS"}])
def test_conflicting_duplicate_order_id_rejects_snapshot(changes):
    with pytest.raises(OpenAlgoResponseError):
        orders_by_id([order(), order(**changes)])


@pytest.mark.parametrize("projection, row, changes", [
    (positions_by_symbol, position(), {"symbol": "sensitive-account-value", "quantity": "private-bad"}),
    (orders_by_id, order(), {"orderid": "sensitive-account-value", "quantity": "private-bad"})])
def test_row_errors_identify_field_and_index_without_echoing_values(projection, row, changes):
    with pytest.raises(OpenAlgoResponseError) as caught:
        projection([row, {**row, **changes}])
    message = str(caught.value)
    assert "row 1" in message and "quantity" in message
    assert "sensitive-account-value" not in message and "private-bad" not in message


@pytest.mark.parametrize("projection", PROJECTIONS)
def test_malformed_text_errors_do_not_echo_payload(projection):
    with pytest.raises(OpenAlgoResponseError) as caught:
        projection("private-account-content-not-json")
    assert "private-account-content" not in str(caught.value)


class Transport:
    def __init__(self):
        self.cash = 1000
        self.positions = response([position()])
        self.orders = response({"orders": [order()]})

    def funds(self):
        return response({"availablecash": self.cash})

    def positionbook(self):
        return self.positions

    def orderbook(self):
        return self.orders

    def place_order(self, **kwargs):
        pytest.fail("Account snapshots must never place orders")

    def cancel_order(self, **kwargs):
        pytest.fail("Account snapshots must never cancel orders")


@pytest.fixture
def account(monkeypatch):
    adapter = Transport()
    broker = OABroker(store=SimpleNamespace(adapter=adapter, get_client=lambda: None), mode="paper")
    monkeypatch.setattr(broker, "_ensure_monitor_started", lambda: None)
    broker.start()
    broker.reconcile_account()
    try:
        yield broker, adapter
    finally:
        broker.stop()


def diagnostic_state(broker):
    return (broker._external_cash, broker._cash_discrepancy, broker._external_positions,
            broker._external_orders, broker._account_discrepancies, broker._last_account_reconcile)


def local_state(broker):
    return (broker.getcash(), broker.getvalue(), broker.startingcash, broker.startingvalue,
            dict(broker._positions), dict(broker._orders), dict(broker._active_orders),
            dict(broker._oidmap), list(broker._notifs))


@pytest.mark.parametrize("endpoint, data", [
    ("positions", None), ("positions", {}), ("positions", [position(), None]),
    ("positions", [position(quantity="NaN")]), ("positions", [position(exchange=None)]),
    ("positions", [position(netqty=3)]), ("orders", {}), ("orders", {"orders": None}),
    ("orders", [order(), None]), ("orders", [order(action="HOLD")]),
    ("orders", [order(), order(quantity=5)]), ("orders", [{"orderid": "id-only"}])])
def test_bad_snapshot_preserves_all_diagnostics_and_local_ledger(account, endpoint, data):
    broker, adapter = account
    before, local = diagnostic_state(broker), local_state(broker)
    adapter.cash = 2500
    setattr(adapter, endpoint, response(data))
    with pytest.raises(OpenAlgoResponseError):
        broker.reconcile_account()
    assert diagnostic_state(broker) == before
    assert local_state(broker) == local
    adapter.positions = response([])
    adapter.orders = response({"orders": []})
    broker.reconcile_account()
    assert broker.get_account_cash() == 2500
    assert broker._external_positions == broker._external_orders == {}
    assert broker._account_discrepancies == {}
    assert broker._last_account_reconcile >= before[-1]
    assert local_state(broker) == local


def test_monitor_survives_invalid_account_evidence_with_backoff_then_recovers(account, monkeypatch):
    broker, adapter = account
    before, local = diagnostic_state(broker), local_state(broker)
    adapter.orders = response({})
    broker._account_reconcile_interval = 0
    now = [broker._request_limiter._clock()]
    monkeypatch.setattr("openalgo_bt.brokers.oabroker.time.monotonic", lambda: now[0])
    observations, backoffs = [], []

    def next_cycle(timeout):
        observations.append(diagnostic_state(broker))
        backoffs.append(broker._monitor_backoff)
        assert timeout == max(broker._monitor_interval, broker._monitor_backoff)
        now[0] += timeout
        if len(observations) == 6:
            adapter.cash = 2000
            adapter.orders = response({"orders": []})
        elif len(observations) == 7:
            broker._monitor_stop.set()

    monkeypatch.setattr(broker._monitor_wakeup, "wait", next_cycle)
    broker._monitor_orders()
    assert observations[:6] == [before] * 6
    assert observations[6][0] == 2000
    assert observations[6][3] == {}
    assert backoffs == [1, 2, 4, 8, 16, 30, 0]
    assert local_state(broker) == local
