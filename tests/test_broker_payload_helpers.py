from types import SimpleNamespace

from openalgo_bt.brokers.oabroker import OABroker


def test_data_symbol_exchange_uses_symbol_prefix_before_default_exchange():
    broker = object.__new__(OABroker)
    broker.p = SimpleNamespace(default_exchange="NSE")
    data = SimpleNamespace(p=SimpleNamespace(symbol="NFO:NIFTY25SEP25000CE", exchange=None), _name="ignored")

    assert broker._data_symbol_exchange(data) == ("NIFTY25SEP25000CE", "NFO")


def test_data_symbol_exchange_allows_explicit_exchange_override():
    broker = object.__new__(OABroker)
    broker.p = SimpleNamespace(default_exchange="NSE")
    data = SimpleNamespace(p=SimpleNamespace(symbol="NFO:NIFTY25SEP25000CE", exchange="NSE"), _name="ignored")

    assert broker._data_symbol_exchange(data) == ("NIFTY25SEP25000CE", "NSE")
