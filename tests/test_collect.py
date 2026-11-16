"""Offline collector tests with synthetic Alpha Vantage HTTP responses."""

import csv
import gzip
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from market_data_tracker import alpha_vantage, collect, history, raw_store
from market_data_tracker.config import STOCKS


TEST_KEY = "synthetic-collector-key"
MONDAY = datetime(2026, 11, 16, 15, 30, tzinfo=timezone.utc)
FRIDAY = datetime(2026, 11, 13, 15, 30, tzinfo=timezone.utc)
SATURDAY = datetime(2026, 11, 14, 15, 30, tzinfo=timezone.utc)


def daily_payload(symbol, trading_date="2026-11-13"):
    """Synthetic test data with one source trading day."""
    return {
        "Meta Data": {"2. Symbol": symbol},
        "Time Series (Daily)": {
            trading_date: {
                "1. open": "101.2500",
                "2. high": "105.0000",
                "3. low": "100.0000",
                "4. close": "103.7500",
                "5. volume": "123456",
            }
        },
    }


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def install_http(monkeypatch, actions=None):
    """Replace all transport calls; no test can contact Alpha Vantage."""
    actions = actions or {}
    requests = []

    def opener(request, timeout):
        symbol = parse_qs(urlsplit(request.full_url).query)["symbol"][0]
        requests.append(symbol)
        action = actions.get(symbol)
        if action == "http403":
            raise HTTPError(request.full_url, 403, "synthetic forbidden", {}, None)
        if action == "http429":
            raise HTTPError(request.full_url, 429, "synthetic limit", {}, None)
        if action == "rate_message":
            return FakeResponse({"Note": "API call frequency limit reached."})
        if action == "invalid_json":
            return FakeResponse(b"{broken")
        if action == "echo_key":
            return FakeResponse({"Error Message": "Invalid API key: {}".format(TEST_KEY)})
        payload = daily_payload(symbol)
        if action == "missing_volume":
            del payload["Time Series (Daily)"]["2026-11-13"]["5. volume"]
        if action == "duplicate_volume":
            raw = json.dumps(payload).replace(
                '"5. volume": "123456"', '"5. volume": "123456", "5. volume": "999999"'
            )
            return FakeResponse(raw.encode("utf-8"))
        return FakeResponse(payload)

    monkeypatch.setattr(alpha_vantage, "urlopen", opener)
    return requests


def run_once(tmp_path, monkeypatch, capsys, actions=None, when=MONDAY):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", TEST_KEY)
    monkeypatch.setattr(raw_store, "_utc_today", lambda: when.date())
    requests = install_http(monkeypatch, actions)
    sleeps = []
    exit_code = collect.collect_once(
        tmp_path,
        clock=lambda: when,
        sleep_fn=sleeps.append,
    )
    return exit_code, requests, sleeps, capsys.readouterr().out


def read_status(tmp_path):
    return json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))


def read_history(tmp_path):
    return list(csv.DictReader(io.StringIO((tmp_path / "history.csv").read_text(encoding="utf-8"))))


def test_all_twelve_succeed_with_one_request_each_and_pacing(tmp_path, monkeypatch, capsys):
    assert len(STOCKS) == 12
    assert len({stock.item_id for stock in STOCKS}) == 12
    assert all(stock.name for stock in STOCKS)
    exit_code, requests, sleeps, output = run_once(tmp_path, monkeypatch, capsys)

    assert exit_code == 0
    assert requests == [stock.item_id for stock in STOCKS]
    assert sleeps == [15] * 11
    assert len(list((tmp_path / "raw" / "2026-11-16").glob("*.json.gz"))) == 12
    assert len(read_history(tmp_path)) == 12
    status = read_status(tmp_path)
    assert status["sources"] == [{"name": "Alpha Vantage", "ok": True, "count": 12}]
    assert status["run_ok"] is True and status["complete"] is True
    assert status["items"] == 12
    assert status["first_run_date"] == "2026-11-16"
    assert status["days_with_success"] == 1
    assert status["run_time"] == "2026-11-16T15:30:00Z"
    assert status["new_today"] == sorted(stock.item_id for stock in STOCKS)
    assert status["gone_today"] == []
    assert "Collection complete: 12/12" in output
    assert TEST_KEY not in output
    assert TEST_KEY not in (tmp_path / "status.json").read_text()
    assert TEST_KEY not in (tmp_path / "history.csv").read_text()
    with gzip.open(tmp_path / "raw" / "2026-11-16" / "alphavantage_AAPL.json.gz", "rb") as saved:
        original_response = saved.read()
    assert original_response == json.dumps(daily_payload("AAPL")).encode("utf-8")
    assert TEST_KEY.encode() not in original_response


def test_one_http_failure_preserves_eleven_successes_and_reports_status(tmp_path, monkeypatch, capsys):
    code, requests, sleeps, output = run_once(tmp_path, monkeypatch, capsys, {"AAPL": "http403"})

    assert code == 1
    assert len(requests) == 12 and len(sleeps) == 11
    assert len(read_history(tmp_path)) == 11
    status = read_status(tmp_path)
    assert status["sources"] == [{"name": "Alpha Vantage", "ok": False, "count": 11}]
    assert status["run_ok"] is True and status["complete"] is False
    assert status["items"] == 11
    assert status["new_today"] == sorted(stock.item_id for stock in STOCKS if stock.item_id != "AAPL")
    assert status["gone_today"] == []
    assert status["failures"][0]["category"] == "http_error"
    assert status["failures"][0]["http_status"] == 403
    assert "AAPL: failed [http_error] (HTTP 403)" in output
    assert TEST_KEY not in output


def test_multiple_api_and_parsing_failures_are_distinct(tmp_path, monkeypatch, capsys):
    actions = {"AAPL": "http429", "MSFT": "invalid_json", "NVDA": "missing_volume"}
    code, requests, _sleeps, output = run_once(tmp_path, monkeypatch, capsys, actions)

    assert code == 1
    assert len(requests) == 12
    assert len(read_history(tmp_path)) == 9
    status = read_status(tmp_path)
    assert status["sources"][0] == {"name": "Alpha Vantage", "ok": False, "count": 9}
    assert [(item["item_id"], item["category"]) for item in status["failures"]] == [
        ("AAPL", "rate_limit"),
        ("MSFT", "invalid_json"),
        ("NVDA", "invalid_data"),
    ]
    assert "HTTP 429" in output


def test_duplicate_key_response_is_rejected_before_storage_without_stopping_others(tmp_path, monkeypatch, capsys):
    code, requests, _sleeps, _output = run_once(tmp_path, monkeypatch, capsys, {"AAPL": "duplicate_volume"})
    assert code == 1 and len(requests) == 12
    assert not (tmp_path / "raw" / "2026-11-16" / "alphavantage_AAPL.json.gz").exists()
    assert len(read_history(tmp_path)) == 11
    result = read_status(tmp_path)
    assert result["sources"][0]["count"] == 11
    assert result["failures"][0]["category"] == "snapshot_validation"
    assert "duplicate JSON object keys" in result["failures"][0]["message"]


def test_all_fail_records_failed_run_without_claiming_success(tmp_path, monkeypatch, capsys):
    actions = {stock.item_id: "http403" for stock in STOCKS}
    code, requests, _sleeps, output = run_once(tmp_path, monkeypatch, capsys, actions)

    assert code == 1 and len(requests) == 12
    assert not (tmp_path / "raw").exists()
    assert not (tmp_path / "history.csv").exists()
    status = read_status(tmp_path)
    assert status["sources"] == [{"name": "Alpha Vantage", "ok": False, "count": 0}]
    assert status["run_ok"] is False
    assert status["first_run_date"] is None
    assert status["days_with_success"] == 0 and status["items"] == 0
    assert status["new_today"] == [] and status["gone_today"] == []
    assert "Collection failed: 0/12" in output


def test_all_failed_retry_preserves_previous_history_and_reports_failure(tmp_path, monkeypatch, capsys):
    run_once(tmp_path, monkeypatch, capsys)
    before = (tmp_path / "history.csv").read_bytes()
    apple = (tmp_path / "raw" / "2026-11-16" / "alphavantage_AAPL.json.gz").read_bytes()
    actions = {stock.item_id: "http403" for stock in STOCKS}
    code, _requests, _sleeps, _output = run_once(tmp_path, monkeypatch, capsys, actions)

    assert code == 1
    assert (tmp_path / "history.csv").read_bytes() == before
    assert (tmp_path / "raw" / "2026-11-16" / "alphavantage_AAPL.json.gz").read_bytes() == apple
    status = read_status(tmp_path)
    assert status["run_ok"] is False
    assert status["sources"][0]["count"] == 0
    assert status["items"] == 12
    assert status["new_today"] == sorted(stock.item_id for stock in STOCKS)
    assert status["gone_today"] == []


def test_failed_same_day_retry_keeps_previous_snapshot_and_no_false_disappearance(tmp_path, monkeypatch, capsys):
    run_once(tmp_path, monkeypatch, capsys)
    apple = tmp_path / "raw" / "2026-11-16" / "alphavantage_AAPL.json.gz"
    previous_bytes = apple.read_bytes()
    code, _requests, _sleeps, _output = run_once(tmp_path, monkeypatch, capsys, {"AAPL": "http403"})

    assert code == 1
    assert apple.read_bytes() == previous_bytes
    assert len(read_history(tmp_path)) == 12
    status = read_status(tmp_path)
    assert status["sources"][0]["count"] == 11
    assert status["items"] == 12
    assert status["new_today"] == sorted(stock.item_id for stock in STOCKS)
    assert status["gone_today"] == []
    first = (tmp_path / "history.csv").read_bytes()
    history.build_history(tmp_path)
    assert (tmp_path / "history.csv").read_bytes() == first


def test_weekend_observation_keeps_friday_trading_date(tmp_path, monkeypatch, capsys):
    run_once(tmp_path, monkeypatch, capsys, when=FRIDAY)
    code, _requests, _sleeps, _output = run_once(tmp_path, monkeypatch, capsys, when=SATURDAY)
    assert code == 0
    rows = read_history(tmp_path)
    assert {row["date"] for row in rows} == {"2026-11-13", "2026-11-14"}
    assert {row["trading_date"] for row in rows} == {"2026-11-13"}
    assert read_status(tmp_path)["new_today"] == []
    assert read_status(tmp_path)["gone_today"] == []


def test_first_valid_observation_after_failed_day_is_new(tmp_path, monkeypatch, capsys):
    run_once(tmp_path, monkeypatch, capsys, {"AAPL": "http403"}, when=FRIDAY)
    code, _requests, _sleeps, _output = run_once(tmp_path, monkeypatch, capsys, when=MONDAY)
    assert code == 0
    result = read_status(tmp_path)
    assert result["days_with_success"] == 2
    assert result["new_today"] == ["AAPL"]
    assert result["gone_today"] == []
    assert len(read_history(tmp_path)) == 23


def test_previously_observed_stock_recovers_without_false_new_or_gone(tmp_path, monkeypatch, capsys):
    run_once(tmp_path, monkeypatch, capsys, when=FRIDAY)
    run_once(tmp_path, monkeypatch, capsys, {"AAPL": "http403"}, when=SATURDAY)
    code, _requests, _sleeps, _output = run_once(tmp_path, monkeypatch, capsys, when=MONDAY)
    assert code == 0
    result = read_status(tmp_path)
    assert result["days_with_success"] == 3
    assert result["new_today"] == []
    assert result["gone_today"] == []


def test_observation_folder_uses_utc_date_across_timezone_boundary(tmp_path, monkeypatch, capsys):
    local_evening = datetime(2026, 11, 15, 20, 30, tzinfo=timezone(timedelta(hours=-6)))
    code, _requests, _sleeps, _output = run_once(tmp_path, monkeypatch, capsys, when=local_evening)
    assert code == 0
    assert (tmp_path / "raw" / "2026-11-16").is_dir()
    assert read_status(tmp_path)["run_time"] == "2026-11-16T02:30:00Z"


def test_run_crossing_utc_midnight_dates_each_saved_response(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", TEST_KEY)
    requests = install_http(monkeypatch)
    before = datetime(2026, 11, 16, 23, 59, tzinfo=timezone.utc)
    after = datetime(2026, 11, 17, 0, 1, tzinfo=timezone.utc)
    moments = iter([before] * 5 + [after] * 8)

    assert collect.collect_once(tmp_path, clock=lambda: next(moments), sleep_fn=lambda _seconds: None) == 0
    assert len(requests) == 12
    assert len(list((tmp_path / "raw" / "2026-11-16").glob("*.json.gz"))) == 5
    assert len(list((tmp_path / "raw" / "2026-11-17").glob("*.json.gz"))) == 7
    assert len(read_history(tmp_path)) == 12
    result = read_status(tmp_path)
    assert result["days_with_success"] == 2
    assert result["items"] == 7
    assert result["run_time"] == "2026-11-17T00:01:00Z"
    assert TEST_KEY not in capsys.readouterr().out


def test_vendor_message_echoing_key_is_sanitized_in_output_and_status(tmp_path, monkeypatch, capsys):
    code, _requests, _sleeps, output = run_once(tmp_path, monkeypatch, capsys, {"AAPL": "echo_key"})
    assert code == 1
    assert TEST_KEY not in output
    assert TEST_KEY not in (tmp_path / "status.json").read_text(encoding="utf-8")
    assert read_status(tmp_path)["failures"][0]["category"] == "api_error"


def test_missing_key_makes_no_request_or_files(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)

    def forbidden_request(*_args, **_kwargs):
        pytest.fail("Missing API key must make no request")

    monkeypatch.setattr(alpha_vantage, "urlopen", forbidden_request)
    assert collect.collect_once(tmp_path, sleep_fn=lambda _seconds: None, clock=lambda: MONDAY) == 1
    assert not list(tmp_path.iterdir())
    assert "missing_api_key" in capsys.readouterr().out


def test_tests_leave_production_data_untouched(tmp_path, monkeypatch, capsys):
    production = Path(__file__).resolve().parents[1] / "data"
    before = production.exists()
    run_once(tmp_path, monkeypatch, capsys)
    assert production.exists() == before
