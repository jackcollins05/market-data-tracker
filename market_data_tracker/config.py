"""Version-controlled stock universe and conservative request pacing."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Stock:
    item_id: str
    name: str


STOCKS = (
    Stock("AAPL", "Apple Inc."),
    Stock("MSFT", "Microsoft Corp."),
    Stock("NVDA", "NVIDIA Corp."),
    Stock("AMZN", "Amazon.com Inc."),
    Stock("GOOGL", "Alphabet Inc. (Class A)"),
    Stock("META", "Meta Platforms Inc."),
    Stock("TSLA", "Tesla Inc."),
    Stock("JPM", "JPMorgan Chase & Co."),
    Stock("BAC", "Bank of America Corp."),
    Stock("WMT", "Walmart Inc."),
    Stock("XOM", "Exxon Mobil Corp."),
    Stock("JNJ", "Johnson & Johnson"),
)

SOURCE_NAME = "Alpha Vantage"
FREE_DAILY_REQUEST_ALLOWANCE = 25
REQUEST_INTERVAL_SECONDS = 15
