"""Synthetic Alpha Vantage examples; every HTTP response is mocked."""

import json
import socket
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from market_data_tracker import alpha_vantage, fetch


TEST_API_KEY = "synthetic-test-key"


@pytest.fixture
def daily_payload():
    """Realistic synthetic JSON shape, not production market data."""
    return {
        "Meta Data": {"1. Information": "Daily Prices", "2. Symbol": "AAPL"},
        "Time Series (Daily)": {
            "2026-11-12": {
                "1. open": "198.0000",
                "2. high": "200.0000",
                "3. low": "197.5000",
                "4. close": "199.0000",
                "5. volume": "8765432",
            },
            "2026-11-13": {
                "1. open": "201.2500",
                "2. high": "204.5000",
                "3. low": "200.0000",
                "4. close": "203.7500",
                "5. volume": "12345678",
            },
        },
    }


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


@pytest.fixture
def mock_daily_response(monkeypatch, daily_payload):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", TEST_API_KEY)
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(daily_payload)

    monkeypatch.setattr(alpha_vantage, "urlopen", opener)
    return requests


def test_valid_response_uses_one_compact_https_request_and_normalizes_values(mock_daily_response):
    record = alpha_vantage.fetch_latest_daily("aapl")

    assert len(mock_daily_response) == 1
    request, timeout = mock_daily_response[0]
    url = urlsplit(request.full_url)
    assert (url.scheme, url.netloc, url.path) == ("https", "www.alphavantage.co", "/query")
    assert parse_qs(url.query) == {
        "function": ["TIME_SERIES_DAILY"],
        "symbol": ["AAPL"],
        "outputsize": ["compact"],
        "datatype": ["json"],
        "apikey": [TEST_API_KEY],
    }
    assert timeout == alpha_vantage.TIMEOUT_SECONDS
    assert record.item_id == record.item == "AAPL"
    assert record.source == "Alpha Vantage"
    assert record.trading_date == "2026-11-13"
    assert date.fromisoformat(record.observation_date_utc)
    assert record.open == Decimal("201.2500")
    assert record.high == Decimal("204.5000")
    assert record.low == Decimal("200.0000")
    assert record.close == Decimal("203.7500")
    assert record.volume == 12345678
    assert record.http_status == 200


@pytest.mark.parametrize("reverse_order", [False, True])
def test_latest_date_is_selected_independent_of_response_order(daily_payload, reverse_order):
    if reverse_order:
        series = daily_payload["Time Series (Daily)"]
        daily_payload["Time Series (Daily)"] = dict(reversed(list(series.items())))
    record = alpha_vantage.normalize_daily("AAPL", daily_payload, 200, TEST_API_KEY)
    assert record.trading_date == "2026-11-13"
    assert record.close == Decimal("203.7500")


@pytest.mark.parametrize("source_field", ["1. open", "2. high", "3. low", "4. close", "5. volume"])
def test_missing_ohlcv_value_is_rejected(daily_payload, source_field):
    del daily_payload["Time Series (Daily)"]["2026-11-13"][source_field]
    with pytest.raises(alpha_vantage.AlphaVantageError) as caught:
        alpha_vantage.normalize_daily("AAPL", daily_payload, 200, TEST_API_KEY)
    assert caught.value.category == "invalid_data"
    assert caught.value.http_status == 200


@pytest.mark.parametrize(
    ("source_field", "bad_value"),
    [
        ("1. open", "NaN"),
        ("2. high", "Infinity"),
        ("3. low", "-1"),
        ("4. close", "not-a-number"),
        ("1. open", "0"),
        ("2. high", None),
        ("3. low", True),
        ("5. volume", "-1"),
        ("5. volume", "1.5"),
        ("5. volume", "NaN"),
    ],
)
def test_invalid_numeric_value_is_rejected(daily_payload, source_field, bad_value):
    daily_payload["Time Series (Daily)"]["2026-11-13"][source_field] = bad_value
    with pytest.raises(alpha_vantage.AlphaVantageError) as caught:
        alpha_vantage.normalize_daily("AAPL", daily_payload, 200, TEST_API_KEY)
    assert caught.value.category == "invalid_data"


def test_inconsistent_ohlc_is_rejected(daily_payload):
    daily_payload["Time Series (Daily)"]["2026-11-13"]["2. high"] = "199.0000"
    with pytest.raises(alpha_vantage.AlphaVantageError, match="inconsistent"):
        alpha_vantage.normalize_daily("AAPL", daily_payload, 200, TEST_API_KEY)


@pytest.mark.parametrize("payload", [{}, {"Time Series (Daily)": {}}, {"Time Series (Daily)": []}])
def test_missing_or_malformed_time_series_is_rejected(payload):
    with pytest.raises(alpha_vantage.AlphaVantageError) as caught:
        alpha_vantage.normalize_daily("AAPL", payload, 200, TEST_API_KEY)
    assert caught.value.category == "invalid_response"


def test_invalid_json_is_categorized(monkeypatch):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", TEST_API_KEY)
    monkeypatch.setattr(alpha_vantage, "urlopen", lambda *_args, **_kwargs: FakeResponse(b"{broken"))
    with pytest.raises(alpha_vantage.AlphaVantageError) as caught:
        alpha_vantage.fetch_latest_daily("AAPL")
    assert caught.value.category == "invalid_json"
    assert caught.value.http_status == 200


@pytest.mark.parametrize(("status", "category"), [(403, "http_error"), (429, "rate_limit")])
def test_http_failures_are_categorized(monkeypatch, status, category):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", TEST_API_KEY)

    def opener(request, timeout):
        raise HTTPError(request.full_url, status, "synthetic failure", {}, None)

    monkeypatch.setattr(alpha_vantage, "urlopen", opener)
    with pytest.raises(alpha_vantage.AlphaVantageError) as caught:
        alpha_vantage.fetch_latest_daily("AAPL")
    assert caught.value.category == category
    assert caught.value.http_status == status
    assert TEST_API_KEY not in str(caught.value)


@pytest.mark.parametrize(("status", "category"), [(403, "http_error"), (429, "rate_limit")])
def test_http_failure_report_shows_status_without_key(monkeypatch, capsys, status, category):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", TEST_API_KEY)

    def opener(request, timeout):
        raise HTTPError(request.full_url, status, "synthetic failure", {}, None)

    monkeypatch.setattr(alpha_vantage, "urlopen", opener)
    assert fetch.main(["--source", "AAPL", "--dry-run"]) == 1
    output = capsys.readouterr().out
    assert "Failure [{}] (HTTP {})".format(category, status) in output
    assert TEST_API_KEY not in output


def test_timeout_is_categorized(monkeypatch):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", TEST_API_KEY)

    def opener(*_args, **_kwargs):
        raise URLError(socket.timeout("synthetic timeout"))

    monkeypatch.setattr(alpha_vantage, "urlopen", opener)
    with pytest.raises(alpha_vantage.AlphaVantageError) as caught:
        alpha_vantage.fetch_latest_daily("AAPL")
    assert caught.value.category == "timeout"
    assert caught.value.http_status is None


def test_api_rate_limit_inside_http_200_is_categorized(monkeypatch):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", TEST_API_KEY)
    monkeypatch.setattr(
        alpha_vantage,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse({"Note": "API call frequency limit reached. Retry later."}),
    )
    with pytest.raises(alpha_vantage.AlphaVantageError) as caught:
        alpha_vantage.fetch_latest_daily("AAPL")
    assert caught.value.category == "rate_limit"
    assert caught.value.http_status == 200


def test_missing_api_key_makes_no_request(monkeypatch, capsys):
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)

    def forbidden_request(*_args, **_kwargs):
        pytest.fail("A request must not be made without an API key")

    monkeypatch.setattr(alpha_vantage, "urlopen", forbidden_request)
    assert fetch.main(["--source", "AAPL", "--dry-run"]) == 1
    assert "missing_api_key" in capsys.readouterr().out


def test_dry_run_prints_record_and_creates_no_files(monkeypatch, mock_daily_response, capsys):
    # The test's temporary directory is inside this repository and is removed afterward.
    project_root = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(dir=project_root) as directory:
        monkeypatch.chdir(directory)
        assert fetch.main(["--source", "AAPL", "--dry-run"]) == 0
        assert list(Path(directory).iterdir()) == []
        monkeypatch.chdir(project_root)

    output = capsys.readouterr().out
    assert "Success: AAPL (HTTP 200)" in output
    assert "Trading date: 2026-11-13" in output
    assert "Open: 201.2500" in output
    assert "Volume: 12345678" in output
    assert TEST_API_KEY not in output
    assert len(mock_daily_response) == 1


def test_api_error_message_is_useful_and_key_is_redacted(monkeypatch, capsys):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", TEST_API_KEY)
    message = "Invalid key {} at https://www.alphavantage.co/query?apikey={}".format(
        TEST_API_KEY, TEST_API_KEY
    )
    monkeypatch.setattr(
        alpha_vantage, "urlopen", lambda *_args, **_kwargs: FakeResponse({"Error Message": message})
    )
    assert fetch.main(["--source", "AAPL", "--dry-run"]) == 1
    output = capsys.readouterr().out
    assert "Failure [api_error] (HTTP 200)" in output
    assert "Invalid key" in output
    assert TEST_API_KEY not in output


def test_dry_run_is_required_for_any_request(monkeypatch):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", TEST_API_KEY)
    with pytest.raises(SystemExit) as caught:
        fetch.main(["--source", "AAPL"])
    assert caught.value.code == 2
