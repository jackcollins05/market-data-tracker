"""Read and normalize the latest Alpha Vantage daily stock observation."""

import json
import os
import re
import socket
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, quote_plus, urlencode
from urllib.request import Request, urlopen


ENDPOINT = "https://www.alphavantage.co/query"
TIMEOUT_SECONDS = 10
SERIES_KEY = "Time Series (Daily)"
SYMBOL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.^-]{0,19}\Z")
PRICE_FIELDS = {"open": "1. open", "high": "2. high", "low": "3. low", "close": "4. close"}


class AlphaVantageError(Exception):
    """A categorized failure safe to display to the user."""

    def __init__(self, category: str, message: str, http_status: Optional[int] = None):
        super().__init__(message)
        self.category = category
        self.http_status = http_status


@dataclass(frozen=True)
class DailyRecord:
    item_id: str
    item: str
    source: str
    trading_date: str
    observation_date_utc: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    http_status: int


def _safe_message(message: str, api_key: str) -> str:
    """Keep API diagnostics useful without exposing credentials or long payloads."""
    clean = message.replace(api_key, "[REDACTED]")
    clean = clean.replace(quote(api_key, safe=""), "[REDACTED]")
    clean = clean.replace(quote_plus(api_key), "[REDACTED]")
    clean = re.sub(r"(?i)(apikey\s*=\s*)[^&\s]+", r"\1[REDACTED]", clean)
    clean = " ".join(clean.split())
    return clean[:300]


def _api_error(payload: Dict[str, Any], api_key: str, http_status: int) -> None:
    for field in ("Error Message", "Note", "Information"):
        if field in payload:
            message = payload[field]
            if not isinstance(message, str) or not message.strip():
                raise AlphaVantageError("invalid_response", "API error message is malformed.", http_status)
            safe = _safe_message(message, api_key)
            category = "rate_limit" if re.search(r"rate|limit|frequency|call volume", message, re.I) else "api_error"
            raise AlphaVantageError(category, safe, http_status)


def _price(bar: Dict[str, Any], field: str, http_status: int) -> Decimal:
    raw = bar.get(PRICE_FIELDS[field])
    if not isinstance(raw, str) or not raw.strip():
        raise AlphaVantageError("invalid_data", "Missing or malformed {} price.".format(field), http_status)
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise AlphaVantageError("invalid_data", "Invalid {} price.".format(field), http_status) from None
    if not value.is_finite() or value <= 0:
        raise AlphaVantageError("invalid_data", "Invalid {} price.".format(field), http_status)
    return value


def _volume(bar: Dict[str, Any], http_status: int) -> int:
    raw = bar.get("5. volume")
    if not isinstance(raw, str) or not re.fullmatch(r"[0-9]+", raw):
        raise AlphaVantageError("invalid_data", "Missing or invalid volume.", http_status)
    return int(raw)


def normalize_daily(symbol: str, payload: Any, http_status: int, api_key: str) -> DailyRecord:
    """Validate a JSON response and select its newest source trading date."""
    if not isinstance(payload, dict):
        raise AlphaVantageError("invalid_response", "JSON root must be an object.", http_status)
    _api_error(payload, api_key, http_status)

    metadata = payload.get("Meta Data")
    if metadata is not None:
        if not isinstance(metadata, dict):
            raise AlphaVantageError("invalid_response", "Metadata must be an object.", http_status)
        reported_symbol = metadata.get("2. Symbol")
        if reported_symbol is not None and (
            not isinstance(reported_symbol, str) or reported_symbol.upper() != symbol
        ):
            raise AlphaVantageError("invalid_response", "Response symbol does not match request.", http_status)

    series = payload.get(SERIES_KEY)
    if not isinstance(series, dict) or not series:
        raise AlphaVantageError("invalid_response", "Daily time series is missing or empty.", http_status)

    dates = []
    for key in series:
        if not isinstance(key, str):
            raise AlphaVantageError("invalid_response", "Trading date is malformed.", http_status)
        try:
            parsed = date.fromisoformat(key)
        except ValueError:
            raise AlphaVantageError("invalid_response", "Trading date is malformed.", http_status) from None
        if parsed.isoformat() != key:
            raise AlphaVantageError("invalid_response", "Trading date is malformed.", http_status)
        dates.append(parsed)

    trading_date = max(dates).isoformat()
    bar = series[trading_date]
    if not isinstance(bar, dict):
        raise AlphaVantageError("invalid_response", "Latest daily observation must be an object.", http_status)
    prices = {field: _price(bar, field, http_status) for field in PRICE_FIELDS}
    if prices["high"] < max(prices["open"], prices["close"]) or prices["low"] > min(
        prices["open"], prices["close"]
    ):
        raise AlphaVantageError("invalid_data", "OHLC prices are inconsistent.", http_status)
    volume = _volume(bar, http_status)

    return DailyRecord(
        item_id=symbol,
        item=symbol,
        source="Alpha Vantage",
        trading_date=trading_date,
        observation_date_utc=datetime.now(timezone.utc).date().isoformat(),
        volume=volume,
        http_status=http_status,
        **prices
    )


def fetch_latest_daily_with_raw(symbol: str) -> Tuple[DailyRecord, bytes]:
    """Make one compact HTTPS request; return the record and original JSON bytes."""
    if not isinstance(symbol, str) or not SYMBOL_PATTERN.fullmatch(symbol.strip()):
        raise AlphaVantageError("invalid_symbol", "Enter a valid stock ticker.")
    symbol = symbol.strip().upper()

    api_key = os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()
    if not api_key:
        raise AlphaVantageError(
            "missing_api_key", "Set ALPHAVANTAGE_API_KEY in your environment before a dry run."
        )

    query = urlencode(
        {"function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": "compact", "datatype": "json", "apikey": api_key}
    )
    request = Request("{}?{}".format(ENDPOINT, query), headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            http_status = response.status
            body = response.read()
    except HTTPError as error:
        category = "rate_limit" if error.code == 429 else "http_error"
        raise AlphaVantageError(category, "Alpha Vantage returned HTTP {}.".format(error.code), error.code) from None
    except (socket.timeout, TimeoutError):
        raise AlphaVantageError("timeout", "Alpha Vantage request timed out.") from None
    except URLError as error:
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            raise AlphaVantageError("timeout", "Alpha Vantage request timed out.") from None
        raise AlphaVantageError("network_error", "Could not reach Alpha Vantage.") from None
    except OSError:
        raise AlphaVantageError("network_error", "Could not reach Alpha Vantage.") from None

    if http_status >= 400:
        category = "rate_limit" if http_status == 429 else "http_error"
        raise AlphaVantageError(category, "Alpha Vantage returned HTTP {}.".format(http_status), http_status)
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError, TypeError):
        raise AlphaVantageError("invalid_json", "Alpha Vantage returned invalid JSON.", http_status) from None
    return normalize_daily(symbol, payload, http_status, api_key), body


def fetch_latest_daily(symbol: str) -> DailyRecord:
    """Keep the existing read-only adapter interface for the dry-run command."""
    record, _raw_json = fetch_latest_daily_with_raw(symbol)
    return record
