import pytest

from openalgo_bt.adapters.openalgo import NormalizedOpenAlgoResponse
from openalgo_bt.brokers.payload import OrderPayloadError, build_order_payload, extract_order_id


BASE = {
    "strategy": "test",
    "product": "MIS",
    "symbol": "RELIANCE",
    "exchange": "NSE",
    "action": "BUY",
    "quantity": 2,
    "price_type": "MARKET",
}


def test_build_order_payload_validates_and_normalizes_limit_stop_order():
    values = dict(BASE)
    values.update(
        action="sell",
        quantity="3",
        price_type="sl",
        price="100.5",
        trigger_price="99.5",
    )
    payload = build_order_payload(**values)

    assert payload == {
        "strategy": "test",
        "product": "MIS",
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "action": "SELL",
        "quantity": 3,
        "price_type": "SL",
        "price": 100.5,
        "trigger_price": 99.5,
    }


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"quantity": 0}, "quantity"),
        ({"price_type": "BRACKET"}, "price type"),
        ({"price_type": "LIMIT", "price": None}, "price"),
        ({"price_type": "SL-M", "trigger_price": 0}, "trigger_price"),
    ],
)
def test_build_order_payload_rejects_unsafe_or_incomplete_orders(override, message):
    values = dict(BASE)
    values.update(override)

    with pytest.raises(OrderPayloadError, match=message):
        build_order_payload(**values)


def test_extract_order_id_supports_normalized_nested_responses():
    response = NormalizedOpenAlgoResponse(
        status="success",
        data={"result": {"orderid": "abc-123"}},
        raw={},
        source_key="data",
    )

    assert extract_order_id(response) == "abc-123"
