
import pytest

from openalgo_bt.adapters.openalgo import NormalizedOpenAlgoResponse
from openalgo_bt.brokers.routing import BrokerRoutingError, validate_routing_mode


class FakeAdapter:
    def __init__(self, mode, analyze_mode):
        self.mode = mode
        self.analyze_mode = analyze_mode
        self.calls = 0

    def analyzer(self):
        self.calls += 1
        return NormalizedOpenAlgoResponse(
            status="success",
            data={"mode": self.mode, "analyze_mode": self.analyze_mode},
            raw={},
            source_key="data",
        )


def validate(mode, adapter, allow_live=False):
    if mode == "backtest":
        validate_routing_mode(mode, {})
        return
    response = adapter.analyzer()
    validate_routing_mode(mode, response.data, allow_live=allow_live)


def test_backtest_mode_cannot_route_upstream():
    adapter = FakeAdapter("analyze", True)
    with pytest.raises(BrokerRoutingError, match="does not route"):
        validate("backtest", adapter)

    assert adapter.calls == 0


def test_paper_mode_requires_analyze_or_paper_target():
    adapter = FakeAdapter("analyze", True)
    validate("paper", adapter)
    assert adapter.calls == 1


@pytest.mark.parametrize(
    ("remote_mode", "analyze_mode"),
    [("live", False), ("analyze", False), ("paper", False)],
)
def test_paper_mode_rejects_non_paper_safe_target(remote_mode, analyze_mode):
    with pytest.raises(BrokerRoutingError, match="Paper mode requires"):
        validate("paper", FakeAdapter(remote_mode, analyze_mode))


def test_live_mode_needs_two_explicit_opt_ins(monkeypatch):
    adapter = FakeAdapter("live", False)
    monkeypatch.delenv("OPENALGO_BT_ALLOW_LIVE", raising=False)

    with pytest.raises(BrokerRoutingError, match="OPENALGO_BT_ALLOW_LIVE"):
        validate("live", adapter, allow_live=True)


def test_live_mode_accepts_only_matching_live_target(monkeypatch):
    adapter = FakeAdapter("live", False)
    monkeypatch.setenv("OPENALGO_BT_ALLOW_LIVE", "1")

    validate("live", adapter, allow_live=True)
    assert adapter.calls == 1
