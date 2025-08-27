import os
from dotenv import load_dotenv
import pandas as pd
from datetime import datetime
from openalgo import api

# Load environment variables from .env file
load_dotenv()

# Initialize OpenAlgo client
client = api(
    api_key=os.getenv('OPENALGO_API_KEY'),  # Set OPENALGO_API_KEY in your .env file
    host='http://127.0.0.1:5000'
)

def history(symbol, start, end_date, interval="D", exchange=None, use_cache=False):
    """
    Generator that yields historical candle data for a symbol from OpenAlgo API.

    If symbol is in "EXCHANGE:SYMBOL" form, uses EXCHANGE as the exchange unless overridden by the exchange parameter.

    Args:
        symbol (str): Stock symbol (e.g., "NSE:TCS" or "NSE_INDEX:NIFTY").
        start (str or datetime): Start date (YYYY-MM-DD or datetime).
        end_date (str or datetime): End date (YYYY-MM-DD or datetime).
        interval (str): Candle interval (e.g., "D", "5m", "15m", "1m"). Default: "D".
        exchange (str): Exchange code (default: "NSE"). If provided, overrides exchange in symbol.
        use_cache (bool): Whether to use local cache (not implemented yet).

    Yields:
        dict: Each candle as a dictionary with keys: datetime, open, high, low, close, volume, etc.
    """
    # Parse symbol for "A:B" form
    parsed_exchange = None
    parsed_symbol = symbol
    if isinstance(symbol, str) and ":" in symbol:
        parts = symbol.split(":", 1)
        if len(parts) == 2:
            parsed_exchange, parsed_symbol = parts[0], parts[1]
    # If exchange is provided (not None/empty), it overrides parsed_exchange
    final_exchange = exchange if exchange else (parsed_exchange if parsed_exchange else "NSE")
    final_symbol = parsed_symbol
    # Convert dates to string if needed
    if isinstance(start, datetime):
        start_str = start.strftime("%Y-%m-%d")
    else:
        start_str = str(start)
    if isinstance(end_date, datetime):
        end_str = end_date.strftime("%Y-%m-%d")
    else:
        end_str = str(end_date)

    # TODO: Implement caching logic here if use_cache is True

    # print(final_symbol)
    # print(final_exchange)
    # Fetch data from OpenAlgo API
    df = client.history(
        symbol=final_symbol,
        exchange=final_exchange,
        interval=interval,
        start_date=start_str,
        end_date=end_str
    )

    if not isinstance(df, pd.DataFrame) or df.empty:
        return  # No data

    # Ensure datetime index and normalize to UTC naive for Backtrader
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    # If index is naive, assume UTC; if tz-aware, convert to UTC
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    # Backtrader expects naive UTC datetime index
    df.index = df.index.tz_localize(None)
    # Ensure chronological order
    df.sort_index(inplace=True)

    # Yield each row as a dict
    for idx, row in df.iterrows():
        candle = row.to_dict()
        candle['datetime'] = idx
        yield candle

from diskcache import Cache

ltpbulk_cache = Cache('/Users/sudranga1/workspace/openalgo_strategies/cache/ltp_bulk/')

@ltpbulk_cache.memoize(name='ltp_bulk')
def fetch_ltp_bulk(symbols, current_date):
    """
    Fetch LTP for all symbols for the given date.
    Returns a dict: symbol -> ltp (float or None)
    """
    ltp_dict = {}
    for symbol in symbols:
        try:
            ltp_dict[symbol] = get_ltp(symbol, current_date)
        except Exception as e:
            ltp_dict[symbol] = None
    return ltp_dict


ltp_cache = Cache('/Users/sudranga1/workspace/openalgo_strategies/cache/ltp/')

@ltp_cache.memoize(name='ltp')
def get_ltp(symbol, current_date):
    """
    Fetch LTP (Close price) for the symbol from OpenAlgo API history for the given date.
    Raises ValueError if LTP cannot be fetched.
    """
    date_str = str(current_date)
    # Acceptable date formats: "YYYY-MM-DD", "YYYY/MM/DD", "YYYYMMDD"
    if "-" in date_str:
        query_date = date_str
    elif "/" in date_str:
        query_date = date_str.replace("/", "-")
    elif len(date_str) == 8:
        query_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    else:
        raise ValueError(f"LTP fetch failed: Unrecognized date format {current_date}")

    try:
        candles = list(history(symbol, query_date, query_date, interval="D"))
        if not candles:
            raise ValueError(f"LTP fetch failed: No candle data for {symbol} on {query_date}")
        close = candles[0].get("close")
        if close is None:
            raise ValueError(f"LTP fetch failed: No close value for {symbol} on {query_date}")
        return float(close)
    except Exception as e:
        print(f"Error fetching LTP from OpenAlgo API for {symbol} on {query_date}: {e}")
        raise ValueError(f"LTP fetch failed: {symbol} on {query_date}: {e}")

# 9:30am price fetcher with cache
morn_cache = Cache('/Users/sudranga1/workspace/openalgo_strategies/cache/930am/')

@morn_cache.memoize(name='930am')
def fetch_930am_price(symbol, date):
    """
    Fetch the 9:30am price for a symbol on a given date using OpenAlgo API.
    Returns the price as float, or None if not found.
    Handles both dict and list response formats.
    """
    import requests
    api_key = os.getenv('OPENALGO_API_KEY')
    api_host = os.getenv('OPENALGO_API_HOST', 'http://127.0.0.1:5000')
    url = f"{api_host}/api/v1/history"

    # print("@"*50)
    payload = {
        "apikey": api_key,
        "symbol": symbol,
        "exchange": "NSE",
        "interval": "15m",
        "start_date": date.strftime("%Y-%m-%d"),
        "end_date": date.strftime("%Y-%m-%d")
    }
    try:
        # print("#"*50)
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # import json
        # print(json.dumps(data, indent=4))
        if isinstance(data, dict):
            if isinstance(data.get("data"), list):
                candles = data["data"]
            elif isinstance(data.get("data"), dict) and isinstance(data["data"].get("candles"), list):
                candles = data["data"]["candles"]
            else:
                candles = []
        elif isinstance(data, list):
            candles = data
        else:
            candles = []
        if not isinstance(candles, list):
            return None
        for candle in candles:
            ts = candle.get("timestamp") if isinstance(candle, dict) else candle[0]
            if isinstance(ts, int):
                from datetime import datetime
                ts_str = datetime.fromtimestamp(ts).strftime("%H:%M")
            elif isinstance(ts, str) and ":" in ts:
                ts_str = ts[-5:]
            else:
                ts_str = ""

            # print(ts_str)
            # print("-"*50)
            if ts_str == "09:30":
                price = candle.get("close") if isinstance(candle, dict) else candle[4]
                if price is not None:
                    return float(price)
    except Exception as e:
        print(f"Error fetching 9:30am price for {symbol} on {date}: {e}")
    return None

# Example usage:
if __name__ == "__main__":
    symbol = "NSE:M&M"
    # symbol = "NSE_INDEX:NIFTY"
    for candle in history(symbol, "2025-08-01", "2025-08-01", interval="D"):
        print(candle)
