import os
import sys
import matplotlib
matplotlib.use("Agg")  # headless backend for safety

import backtrader as bt
from backtrader import TimeFrame
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from openalgo_bt.stores.oa import OAStore
from openalgo_bt.feeds.oa import OAData


class TestOABrokerStrategy(bt.Strategy):
    params = dict(
        buy_size=1,
        make_limit_exit=True,
    )

    def __init__(self):
        self.ordered = False
        self.orders_submitted = False
        self.order_refs = []

    def log(self, txt):
        print(f"[{self.datas[0]._name}] {txt}")

    def notify_order(self, order):
        # Order status notifications from the broker
        if order.status in [order.Submitted, order.Accepted]:
            self.log(f"ORDER {order.ref} {order.getordername()} {order.getstatusname()}")
        elif order.status in [order.Completed]:
            if order.isbuy():
                self.log(f"BUY COMPLETED ref={order.ref} price={order.executed.price} size={order.executed.size}")
            else:
                self.log(f"SELL COMPLETED ref={order.ref} price={order.executed.price} size={order.executed.size}")
        elif order.status in [order.Canceled]:
            self.log(f"ORDER CANCELED ref={order.ref}")
        elif order.status in [order.Rejected]:
            self.log(f"ORDER REJECTED ref={order.ref}")

    def start(self):
        self.log("Strategy start() called")

    def next(self):
        dt_utc = bt.num2date(self.data.datetime[0])
        # Convert UTC (or naive assumed UTC) to IST for logging
        if getattr(dt_utc, "tzinfo", None) is None:
            dt_ist = dt_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Asia/Calcutta"))
        else:
            dt_ist = dt_utc.astimezone(ZoneInfo("Asia/Calcutta"))
        self.log(f"next() {dt_ist.strftime('%Y-%m-%d %H:%M:%S %Z')} O={self.data.open[0]} H={self.data.high[0]} L={self.data.low[0]} C={self.data.close[0]}")

        if not self.orders_submitted:
            close = float(self.data.close[0])
            # size = int(self.p.buy_size)

            print(self.data)
            print("~"*50)

            # NOTE: THE PRICES HAVE BEEN SELECTED SUCH THAT ORDERS ARE ACCEPTED BUT DO NOT EXECUTE>>>>
            
            size = 1

            # # WORKS!
            # limit_price_buy = round_to_tick_size(close * 0.95) # 390.0
            # o3 = self.buy(data=self.data, size=size, exectype=bt.Order.Limit, price=limit_price_buy)
            # if o3 is not None:
            #     self.log(f"Submitted BUY LIMIT at {limit_price_buy:.2f} ref={o3.ref}")
            #     self.order_refs.append(('BUY', 'LIMIT', o3.ref))
            # else:
            #     self.log("Failed to submit BUY LIMIT order")

            # # WORKS! - Error out : Your order price is higher than the current [upper circuit limit]
            # limit_price_sell = round_to_tick_size(close * 1.05) # 500.0 
            # o4 = self.sell(data=self.data, size=size, exectype=bt.Order.Limit, price=limit_price_sell)
            # if o4 is not None:
            #     self.log(f"Submitted SELL LIMIT at {limit_price_sell:.2f} ref={o4.ref}")
            #     self.order_refs.append(('SELL', 'LIMIT', o4.ref))
            # else:
            #     self.log("Failed to submit SELL LIMIT order")

            # Market Orders
            # WORKS! - MIS , MARKET
            # o1 = self.buy(data=self.data, size=size, exectype=bt.Order.Market)
            # if o1 is not None:
            #     self.log(f"Submitted BUY MARKET size={size} ref={o1.ref}")
            #     self.order_refs.append(('BUY', 'MARKET', o1.ref))
            # else:
            #     self.log("Failed to submit BUY MARKET order")

            # WORKS! 
            # o2 = self.sell(data=self.data, size=size, exectype=bt.Order.Market)
            # if o2 is not None:
            #     self.log(f"Submitted SELL MARKET size={size} ref={o2.ref}")
            #     self.order_refs.append(('SELL', 'MARKET', o2.ref))
            # else:
            #     self.log("Failed to submit SELL MARKET order")


            # # StopLimit (Trigger Limit) Orders (trigger price, then limit order at plimit)
            # stoplimit_trigger_buy = round_to_tick_size(close * 1.03)
            # stoplimit_limit_buy = round_to_tick_size(close * 1.035)
            # stoplimit_trigger_sell = round_to_tick_size(close * 0.95)
            # stoplimit_limit_sell = round_to_tick_size(close * 0.945)
            # o7 = self.buy(data=self.data, size=size, exectype=bt.Order.StopLimit, price=stoplimit_trigger_buy, plimit=stoplimit_limit_buy)
            # if o7 is not None:
            #     self.log(f"Submitted BUY STOPLIMIT (Trigger Limit) trigger={stoplimit_trigger_buy:.2f} limit={stoplimit_limit_buy:.2f} ref={o7.ref}")
            #     self.order_refs.append(('BUY', 'STOPLIMIT', o7.ref))
            # else:
            #     self.log("Failed to submit BUY STOPLIMIT (Trigger Limit) order")

            # o8 = self.sell(data=self.data, size=size, exectype=bt.Order.StopLimit, price=stoplimit_trigger_sell, plimit=stoplimit_limit_sell)
            # if o8 is not None:
            #     self.log(f"Submitted SELL STOPLIMIT (Trigger Limit) trigger={stoplimit_trigger_sell:.2f} limit={stoplimit_limit_sell:.2f} ref={o8.ref}")
            #     self.order_refs.append(('SELL', 'STOPLIMIT', o8.ref))
            # else:
            #     self.log("Failed to submit SELL STOPLIMIT (Trigger Limit) order")


            # WORKS! - Bracket BUY example: limit entry below current close, SL below entry, TP above entry
            # entry_price_buy = round_to_tick_size(close * 0.99)
            # sl_price_buy = round_to_tick_size(entry_price_buy * 0.97)   # ~3% SL below entry
            # tp_price_buy = round_to_tick_size(entry_price_buy * 1.02)   # ~5% TP above entry

            # try:
            #     oentry_b, ostp_b, otp_b = self.buy_bracket(
            #         data=self.data,
            #         size=size,
            #         price=entry_price_buy,
            #         exectype=bt.Order.Limit,
            #         stopprice=sl_price_buy,
            #         stopexec=bt.Order.Stop,
            #         limitprice=tp_price_buy,
            #         limitexec=bt.Order.Limit,
            #     )
            #     if oentry_b is not None:
            #         self.log(f"Submitted BUY BRACKET parent LIMIT at {entry_price_buy:.2f} ref={oentry_b.ref}")
            #         self.order_refs.append(('BUY', 'BRACKET_PARENT_LIMIT', oentry_b.ref))
            #     if ostp_b is not None:
            #         self.log(f"Submitted BUY BRACKET child STOP (SL-M) at trigger {sl_price_buy:.2f} ref={ostp_b.ref}")
            #         self.order_refs.append(('BUY', 'BRACKET_CHILD_STOP', ostp_b.ref))
            #     if otp_b is not None:
            #         self.log(f"Submitted BUY BRACKET child LIMIT (TP) at {tp_price_buy:.2f} ref={otp_b.ref}")
            #         self.order_refs.append(('BUY', 'BRACKET_CHILD_TP', otp_b.ref))
            # except Exception as e:
            #     self.log(f"Failed to submit BUY BRACKET orders: {e!r}")


            # # FIXED BRACKET SELL LOGIC! WORKS with below settings - with caveat that the values should not breach circuit limits..
            # # For a SELL bracket:
            # # - Entry: SELL LIMIT above current price (wait for price to rise before selling)
            # # - Stop Loss: BUY STOP above entry (if price keeps rising, cut losses)  
            # # - Take Profit: BUY LIMIT below entry (if price drops after selling, take profit)
            
            # # But we need to set prices such that orders don't execute immediately!
            # entry_price_sell = round_to_tick_size(close * 1.05)  # SELL at 10% above current (won't execute immediately)
            # sl_price_sell = round_to_tick_size(entry_price_sell * 1.03)  # SL at 5% above entry (cut losses if price rises further)
            # tp_price_sell = round_to_tick_size(close * 0.95)  # TP at 5% below CURRENT price (take profit if price drops)

            # print("bracket_sell FIXED: ", entry_price_sell, sl_price_sell, tp_price_sell)
            # print(f"Current close: {close}, Entry: {entry_price_sell}, SL: {sl_price_sell}, TP: {tp_price_sell}")
            
            # try:
            #     oentry_s, ostp_s, otp_s = self.sell_bracket(
            #         data=self.data,
            #         size=size,
            #         price=entry_price_sell,
            #         exectype=bt.Order.Limit,
            #         stopprice=sl_price_sell,
            #         stopexec=bt.Order.Stop,
            #         limitprice=tp_price_sell,
            #         limitexec=bt.Order.Limit,
            #     )
            #     if oentry_s is not None:
            #         self.log(f"Submitted SELL BRACKET parent LIMIT at {entry_price_sell:.2f} ref={oentry_s.ref}")
            #         self.order_refs.append(('SELL', 'BRACKET_PARENT_LIMIT', oentry_s.ref))
            #     if ostp_s is not None:
            #         self.log(f"Submitted SELL BRACKET child STOP (SL-M) at trigger {sl_price_sell:.2f} ref={ostp_s.ref}")
            #         self.order_refs.append(('SELL', 'BRACKET_CHILD_STOP', ostp_s.ref))
            #     if otp_s is not None:
            #         self.log(f"Submitted SELL BRACKET child LIMIT (TP) at {tp_price_sell:.2f} ref={otp_s.ref}")
            #         self.order_refs.append(('SELL', 'BRACKET_CHILD_TP', otp_s.ref))
            # except Exception as e:
            #     self.log(f"Failed to submit SELL BRACKET orders: {e!r}")

            # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
            # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
            # NOTE: SL-M orders are mostly blocked in India
            # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
            # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


            # # Stop Loss (SL-M) Orders (trigger price, market order)
            # stop_price_buy = round_to_tick_size(close * 1.01)
            # stop_price_sell = round_to_tick_size(close * 0.99)
            # o5 = self.buy(data=self.data, size=size, exectype=bt.Order.Stop, price=stop_price_buy)
            # if o5 is not None:
            #     self.log(f"Submitted BUY STOP (SL-M) at trigger {stop_price_buy:.2f} ref={o5.ref}")
            #     self.order_refs.append(('BUY', 'STOP', o5.ref))
            # else:
            #     self.log("Failed to submit BUY STOP (SL-M) order")

            # o6 = self.sell(data=self.data, size=size, exectype=bt.Order.Stop, price=stop_price_sell)
            # if o6 is not None:
            #     self.log(f"Submitted SELL STOP (SL-M) at trigger {stop_price_sell:.2f} ref={o6.ref}")
            #     self.order_refs.append(('SELL', 'STOP', o6.ref))
            # else:
            #     self.log("Failed to submit SELL STOP (SL-M) order")
            # ~~~~~~~~~~~~~~~~~~~~

            self.orders_submitted = True

    def stop(self):
        self.log("Strategy stop() called")


def main():
    # Pre-flight checks
    if not os.getenv("OPENALGO_API_KEY"):
        print("WARNING: OPENALGO_API_KEY not set; broker will fallback to local simulation and may not place real orders.")

    cerebro = bt.Cerebro()

    # Create OA Store and broker
    store = OAStore()
    broker = store.getbroker(product="MIS", strategy="Backtrader Test OA Broker", debug=False)
    cerebro.setbroker(broker)

    # Create OA data feed for a short historical window
    # Adjust symbol and dates as needed
    symbol = os.getenv("OA_TEST_SYMBOL", "NSE:ITC")
    todate = datetime.utcnow()
    fromdate = todate - timedelta(days=1)

    data = OAData(
        symbol=symbol,
        timeframe=TimeFrame.Minutes,
        compression=1,
        fromdate=fromdate,
        # todate=todate,
        # For intraday test instead:
        # timeframe=TimeFrame.Minutes, compression=1,
        # fromdate=datetime(2025, 8, 8, 9, 15), todate=datetime(2025, 8, 8, 15, 30),
        live=True,
    )
    cerebro.adddata(data, name=symbol)

    # Add test strategy
    cerebro.addstrategy(TestOABrokerStrategy, buy_size=1, make_limit_exit=True)

    # Run
    print("Running backbroker_test_oa_broker with OAData + OABroker ...")
    cerebro.run(runonce=False, preload=True)
    print("Run completed.")

    # Save a quick plot (optional)
    # cerebro.plot(style="candlestick", iplot=False, volume=True, savefig=True, figfilename="oa_broker_test_plot.png")


if __name__ == "__main__":
    main()
