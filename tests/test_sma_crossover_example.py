"""Real-Cerebro execution and read-only history tests for the local example."""

import importlib.util
import json
import math
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import backtrader as bt
import pandas as pd
import pytest

import openalgo_bt.adapters.openalgo as adapter_module


SPEC = importlib.util.spec_from_file_location(
    "sma_crossover_example", Path(__file__).parents[1] / "examples" / "sma_crossover_5m.py",
)
example = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = example
SPEC.loader.exec_module(example)


@pytest.fixture(autouse=True)
def no_network_or_dotenv(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Example must not access network or dotenv in offline tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(adapter_module, "load_dotenv", forbidden)
    monkeypatch.setattr(adapter_module, "openalgo_api", forbidden)
    monkeypatch.setenv("OPENALGO_BT_MODE", "live")
    monkeypatch.setenv("OPENALGO_BT_ALLOW_LIVE", "1")


def test_synthetic_data_is_repeatable_and_aligned_to_ist_weekday_sessions():
    frame = example.synthetic_candles(600)
    pd.testing.assert_frame_equal(frame, example.synthetic_candles(600))
    assert frame.index.tz is None
    assert frame.index.is_monotonic_increasing
    local = frame.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
    assert all(ts.weekday() < 5 and ts.minute % 5 == 0 for ts in local)
    assert min(ts.time().isoformat() for ts in local) == "09:15:00"
    assert max(ts.time().isoformat() for ts in local) == "15:25:00"
    assert len(set(local.date)) == 8
    assert local[75].date() > local[74].date()


@pytest.mark.parametrize("broker", ["backtrader", "oa-sim"])
def test_both_brokers_complete_crossover_round_trips_with_consistent_accounting(broker):
    frame = example.synthetic_candles()
    report = example.run_simulation(frame, broker=broker, quantity=3)
    assert report["bars"] == 300
    assert report["closed_trades"] > 0
    assert report["position"] == 0
    assert report["pending_order"] is None
    assert len(report["signals"]) == len(report["fills"]) == 2 * report["closed_trades"]
    assert [f["side"] for f in report["fills"]] == ["BUY", "SELL"] * report["closed_trades"]
    assert all(f["size"] == (3 if f["side"] == "BUY" else -3) for f in report["fills"])
    assert report["cash"] == pytest.approx(100_000 - sum(f["size"] * f["price"] for f in report["fills"]))
    assert report["value"] == report["cash"]
    assert math.isfinite(report["max_drawdown_pct"])
    assert report["max_drawdown_pct"] >= 0
    for signal, fill in zip(report["signals"], report["fills"]):
        assert signal["ref"] == fill["ref"]
        signaled_at = pd.Timestamp(signal["datetime_utc"])
        filled_at = pd.Timestamp(fill["datetime_utc"])
        if broker == "oa-sim":
            assert filled_at == signaled_at
            assert fill["price"] == pytest.approx(signal["close"], rel=1e-12)
        else:
            next_bar = frame.index[frame.index.get_loc(signaled_at) + 1]
            assert filled_at == next_bar
            assert fill["price"] == frame.loc[next_bar, "open"]
        assert [s["status"] for s in report["statuses"] if s["ref"] == signal["ref"]] == [
            "Submitted", "Accepted", "Completed",
        ]


@pytest.mark.parametrize("broker", ["backtrader", "oa-sim"])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_final_bar_order_distinguishes_pending_from_undelivered_execution(broker, side):
    frame = example.synthetic_candles()
    complete = example.run_simulation(frame)
    signal = next(s for s in complete["signals"] if s["side"] == side)
    frame = frame.loc[:signal["datetime_utc"]]
    report = example.run_simulation(frame, broker=broker)
    snapshot = report["undelivered_order"]
    assert snapshot["ref"] == report["signals"][-1]["ref"]
    assert not any(f["ref"] == snapshot["ref"] for f in report["fills"])
    assert report["value"] == pytest.approx(report["cash"] + report["position"] * frame.iloc[-1]["close"])
    if broker == "backtrader":
        assert report["pending_order"] == snapshot["ref"]
        assert snapshot["status"] == "Submitted"
        assert snapshot["executed_size"] == 0
        assert report["position"] == (0 if side == "BUY" else 1)
        executions = report["fills"]
    else:
        assert report["pending_order"] is None
        assert snapshot["status"] == "Completed"
        assert snapshot["executed_size"] == (1 if side == "BUY" else -1)
        assert snapshot["executed_price"] == pytest.approx(frame.iloc[-1]["close"])
        assert report["position"] == (1 if side == "BUY" else 0)
        executions = report["fills"] + [{"size": snapshot["executed_size"], "price": snapshot["executed_price"]}]
    assert report["cash"] == pytest.approx(100_000 - sum(f["size"] * f["price"] for f in executions))


def test_cli_warns_about_undelivered_final_execution(monkeypatch, capsys):
    frame = example.synthetic_candles()
    signal = example.run_simulation(frame)["signals"][0]
    monkeypatch.setattr(example, "synthetic_candles", lambda bars: frame.loc[:signal["datetime_utc"]])
    assert example.main(["--broker", "oa-sim"]) == 0
    output = capsys.readouterr().out
    assert "Final order awaiting strategy notification: Completed" in output
    assert "analyzers include delivered notifications only" in output


def test_execution_models_are_explicitly_different():
    frame = example.synthetic_candles()
    standard = example.run_simulation(frame)
    oa = example.run_simulation(frame, broker="oa-sim")
    assert standard["fill_model"] == "next_bar_open"
    assert oa["fill_model"] == "immediate_current_close"
    assert standard["fills"][0]["price"] != oa["fills"][0]["price"]
    assert standard["cash"] != oa["cash"]


@pytest.mark.parametrize("broker", ["backtrader", "oa-sim"])
def test_open_position_is_marked_not_artificially_liquidated(broker):
    frame = example.synthetic_candles(50)
    report = example.run_simulation(frame, broker=broker)
    assert report["position"] == 1
    assert report["closed_trades"] == 0
    assert len(report["fills"]) == 1
    assert report["value"] == pytest.approx(report["cash"] + frame.iloc[-1]["close"])
    assert report["pending_order"] is None


@pytest.mark.parametrize("broker", ["backtrader", "oa-sim"])
def test_no_crossover_reports_zero_trades(broker):
    frame = example.synthetic_candles(30)
    frame[["open", "high", "low", "close"]] = 100.0
    report = example.run_simulation(frame, broker=broker)
    assert report["closed_trades"] == 0
    assert report["fills"] == report["signals"] == []
    assert report["cash"] == report["value"] == 100_000
    assert report["position"] == 0
    assert report["max_drawdown_pct"] == 0


def test_margin_rejection_clears_pending_and_allows_later_signals():
    report = example.run_simulation(example.synthetic_candles(), cash=1)
    assert report["fills"] == []
    assert len(report["signals"]) > 1
    assert report["pending_order"] is None
    assert any(s["status"] == "Margin" for s in report["statuses"])


@pytest.mark.parametrize("status", [bt.Order.Completed, bt.Order.Canceled, bt.Order.Margin,
                                   bt.Order.Rejected, bt.Order.Expired])
def test_terminal_notice_clears_only_matching_pending_order(status):
    state = SimpleNamespace(pending=SimpleNamespace(ref=10), statuses=[], fills=[])
    order = SimpleNamespace(
        ref=11, status=status, getstatusname=lambda: "Terminal", isbuy=lambda: True,
        executed=SimpleNamespace(dt=bt.date2num(pd.Timestamp("2026-01-05")), price=100, size=1),
        Completed=bt.Order.Completed, Canceled=bt.Order.Canceled, Margin=bt.Order.Margin,
        Rejected=bt.Order.Rejected, Expired=bt.Order.Expired,
    )
    example.SMACrossover.notify_order(state, order)
    assert state.pending.ref == 10
    order.ref = 10
    example.SMACrossover.notify_order(state, order)
    assert state.pending is None


def test_pending_guard_does_not_submit_another_order():
    state = SimpleNamespace(pending=object(), buy=Mock(), close=Mock())
    example.SMACrossover.next(state)
    state.buy.assert_not_called()
    state.close.assert_not_called()


@pytest.mark.parametrize("options", [
    {"broker": "live"}, {"broker": "paper"}, {"cash": float("nan")},
    {"cash": float("inf")}, {"cash": 0}, {"cash": -1}, {"fast": 20},
    {"fast": 0}, {"slow": 0}, {"quantity": 0}, {"quantity": 1.5}, {"fast": True},
])
def test_invalid_simulation_options_are_rejected(options):
    with pytest.raises(ValueError):
        example.run_simulation(example.synthetic_candles(), **options)


@pytest.mark.parametrize("bars", [0, -1, True, 3.5])
def test_invalid_synthetic_bar_counts_are_rejected(bars):
    with pytest.raises(ValueError):
        example.synthetic_candles(bars)


@pytest.mark.parametrize("defect", ["empty", "short", "column", "duplicate", "alignment",
                                  "nan", "inf", "volume", "price", "range", "index"])
def test_invalid_candles_are_rejected(defect):
    frame = example.synthetic_candles(30)
    if defect == "empty":
        frame = frame.iloc[:0]
    elif defect == "short":
        frame = frame.iloc[:21]
    elif defect == "column":
        frame = frame.drop(columns="close")
    elif defect == "duplicate":
        frame.index = pd.DatetimeIndex([frame.index[0]] * len(frame))
    elif defect == "alignment":
        frame.index += pd.Timedelta(minutes=1)
    elif defect in {"nan", "inf"}:
        frame.iloc[0, frame.columns.get_loc("close")] = float(defect)
    elif defect == "volume":
        frame.iloc[0, frame.columns.get_loc("volume")] = -1
    elif defect == "price":
        frame.iloc[0, frame.columns.get_loc("open")] = 0
    elif defect == "range":
        frame.iloc[0, frame.columns.get_loc("high")] = 1
    elif defect == "index":
        frame = frame.reset_index()
    with pytest.raises(ValueError):
        example.run_simulation(frame)


def test_timezone_aware_unsorted_data_is_copied_and_normalized():
    frame = example.synthetic_candles()
    frame.index = frame.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
    frame = frame.iloc[::-1]
    original = frame.copy()
    report = example.run_simulation(frame)
    assert report["closed_trades"] > 0
    pd.testing.assert_frame_equal(frame, original)


def test_history_uses_native_5m_rest_only_then_simulates_locally(monkeypatch):
    frame = example.synthetic_candles(100)
    calls = []

    def fake_post(url, body, timeout):
        calls.append((url, body, timeout))
        rows = frame.reset_index().to_dict("records")
        for row in rows:
            row["datetime"] = row["datetime"].isoformat() + "+00:00"
        return {"status": "success", "data": rows}

    monkeypatch.setenv("OPENALGO_API_KEY", "offline-key")
    monkeypatch.setenv("OPENALGO_API_HOST", "http://offline.invalid/")
    monkeypatch.setattr(adapter_module.OpenAlgoAdapter, "_post_json", staticmethod(fake_post))
    fetched = example.historical_candles("NSE:RELIANCE", "2026-01-05", "2026-01-06")
    for broker in ("backtrader", "oa-sim"):
        assert example.run_simulation(fetched, broker=broker)["closed_trades"] > 0
    assert calls == [("http://offline.invalid/api/v1/history", {
        "apikey": "offline-key", "symbol": "RELIANCE", "exchange": "NSE",
        "interval": "5m", "start_date": "2026-01-05", "end_date": "2026-01-06",
    }, 10.0)]


@pytest.mark.parametrize("symbol,start,end", [
    ("RELIANCE", "2026-01-05", "2026-01-06"),
    ("NSE:", "2026-01-05", "2026-01-06"),
    (":RELIANCE", "2026-01-05", "2026-01-06"),
    ("NSE:RELIANCE", None, None),
    ("NSE:RELIANCE", "2026-01-06", "2026-01-05"),
    ("NSE:RELIANCE", "invalid", "2026-01-06"),
])
def test_invalid_history_options_fail_before_adapter_creation(monkeypatch, symbol, start, end):
    constructor = Mock(side_effect=AssertionError("Adapter must not be constructed"))
    monkeypatch.setattr(example, "OpenAlgoAdapter", constructor)
    with pytest.raises(ValueError):
        example.historical_candles(symbol, start, end)
    constructor.assert_not_called()


def test_history_requires_exported_key(monkeypatch):
    monkeypatch.delenv("OPENALGO_API_KEY", raising=False)
    with pytest.raises(ValueError, match="Export OPENALGO_API_KEY"):
        example.historical_candles("NSE:RELIANCE", "2026-01-05", "2026-01-06")


def test_empty_history_does_not_fall_back_to_synthetic(monkeypatch):
    monkeypatch.setenv("OPENALGO_API_KEY", "offline-key")
    monkeypatch.setattr(adapter_module.OpenAlgoAdapter, "_post_json", staticmethod(
        lambda *args: {"status": "success", "data": []},
    ))
    with pytest.raises(ValueError, match="no candles"):
        example.historical_candles("NSE:RELIANCE", "2026-01-05", "2026-01-06")


@pytest.mark.parametrize("load_env", [True, False])
def test_adapter_dotenv_opt_out_preserves_default(monkeypatch, load_env):
    loader = Mock()
    monkeypatch.setattr(adapter_module, "load_dotenv", loader)
    kwargs = {} if load_env else {"load_env": False}
    adapter_module.OpenAlgoAdapter(api_key="offline-key", client=object(), **kwargs)
    assert loader.call_count == int(load_env)


@pytest.mark.parametrize("broker", ["backtrader", "oa-sim"])
def test_cli_json_modes_work_without_credentials(broker, capsys, monkeypatch):
    monkeypatch.delenv("OPENALGO_API_KEY", raising=False)
    assert example.main(["--broker", broker, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["source"] == "synthetic"
    assert report["symbol"] == "NSE:SYNTHETIC"
    assert report["broker"] == broker
    assert report["closed_trades"] > 0


def test_cli_text_explains_simulation_and_reports_drawdown(capsys):
    assert example.main(["--broker", "oa-sim"]) == 0
    output = capsys.readouterr().out
    assert "no upstream orders" in output
    assert "not realistic execution" in output
    assert "Maximum drawdown:" in output


@pytest.mark.parametrize("args", [["--broker", "live"], ["--source", "openalgo"],
                                  ["--start", "2026-01-05"], ["--bars", "10"]])
def test_cli_rejects_unsafe_or_incomplete_options(args):
    with pytest.raises(SystemExit) as caught:
        example.main(args)
    assert caught.value.code == 2
