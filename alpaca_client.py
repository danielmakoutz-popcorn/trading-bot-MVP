import os
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient

def make_clients():
    """
    Creates both a trading client and a data client using
    environment variables ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY
    """
    api_key = os.getenv("APCA_API_KEY_ID")
    api_secret = os.getenv("APCA_API_SECRET_KEY")

    if not api_key or not api_secret:
        raise ValueError("Missing Alpaca API keys! Set APCA_API_KEY_ID and APCA_API_SECRET_KEY as environment variables.")

    trading = TradingClient(api_key, api_secret, paper=True)
    data = StockHistoricalDataClient(api_key, api_secret)
    return trading, data
