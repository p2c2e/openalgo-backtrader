from openalgo_bt.brokers.oabroker import OABroker


def test_parse_status_normalizes_supported_terminal_states_and_prices():
    assert OABroker._parse_status({"data": {"order_status": "complete", "average_price": 101.25}}) == (
        "COMPLETE",
        101.25,
    )
    assert OABroker._parse_status({"status": "cancelled", "price": "101.25"}) == ("CANCELLED", None)
    assert OABroker._parse_status("rejected") == ("REJECTED", None)


def test_parse_status_does_not_treat_non_numeric_price_as_execution_price():
    assert OABroker._parse_status({"data": {"order_status": "open", "average_price": "101.25"}}) == (
        "OPEN",
        None,
    )
