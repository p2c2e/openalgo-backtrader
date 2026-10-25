from openalgo_bt.adapters.openalgo import NormalizedOpenAlgoResponse
from openalgo_bt.brokers.account import account_discrepancies, available_cash, positions_by_symbol


def response(data):
    return NormalizedOpenAlgoResponse(status="success", data=data, raw={}, source_key="data")


def test_available_cash_prefers_verified_openalgo_field():
    assert available_cash(response({"availablecash": "1234.50", "net": "999"})) == 1234.5
    assert available_cash(response({"unexpected": 1})) is None


def test_positions_by_symbol_aggregates_normalized_position_rows():
    payload = response(
        [
            {"exchange": "NSE", "symbol": "RELIANCE", "quantity": "2"},
            {"exchange": "NSE", "symbol": "RELIANCE", "net_quantity": -1},
            {"exchange": "NFO", "tradingsymbol": "NIFTY25SEP25000CE", "netqty": "3"},
        ]
    )

    assert positions_by_symbol(payload) == {
        ("NSE", "RELIANCE"): 1.0,
        ("NFO", "NIFTY25SEP25000CE"): 3.0,
    }


def test_account_discrepancies_reports_missing_and_mismatched_positions():
    assert account_discrepancies(
        {("NSE", "RELIANCE"): 1},
        {("NSE", "RELIANCE"): 2, ("NSE", "INFY"): 1},
    ) == {
        ("NSE", "RELIANCE"): (1, 2),
        ("NSE", "INFY"): (0.0, 1),
    }
