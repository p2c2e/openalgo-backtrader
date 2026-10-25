from openalgo_bt.brokers.reconciliation import summarize_order_trades


def test_summarize_order_trades_uses_weighted_price_and_deduplicates_trade_ids():
    trades = [
        {"orderid": "order-1", "tradeid": "fill-1", "quantity": "2", "average_price": "100"},
        {"orderid": "order-1", "tradeid": "fill-1", "quantity": "2", "average_price": "100"},
        {"orderid": "order-1", "tradeid": "fill-2", "quantity": "1", "price": "110"},
        {"orderid": "other", "tradeid": "fill-3", "quantity": "9", "price": "1"},
    ]

    summary = summarize_order_trades(trades, "order-1")

    assert summary.quantity == 3
    assert summary.average_price == 103.33333333333333
    assert summary.trade_count == 2


def test_summarize_order_trades_keeps_rows_without_stable_execution_ids():
    trades = [
        {"orderid": "order-1", "quantity": 1, "price": 100},
        {"orderid": "order-1", "quantity": 1, "price": 101},
        {"orderid": "order-1", "tradeid": "broken", "quantity": "bad", "price": 102},
    ]

    summary = summarize_order_trades(trades, "order-1")

    assert summary.quantity == 2
    assert summary.average_price == 100.5
    assert summary.trade_count == 2
