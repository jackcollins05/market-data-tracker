"""Offline tests for required status fields and atomic status replacement."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from market_data_tracker import raw_store, status
from market_data_tracker.config import STOCKS


def snapshot(symbol):
    """Synthetic valid JSON; never fetched from a live source."""
    return json.dumps(
        {
            "Meta Data": {"2. Symbol": symbol},
            "Time Series (Daily)": {
                "2026-11-13": {
                    "1. open": "101.2500",
                    "2. high": "105.0000",
                    "3. low": "100.0000",
                    "4. close": "103.7500",
                    "5. volume": "123456",
                }
            },
        }
    ).encode("utf-8")


def test_required_status_keys_types_dates_and_counts_come_from_raw(tmp_path):
    raw_store.save_snapshot(tmp_path, "2026-11-13", "AAPL", snapshot("AAPL"))
    raw_store.save_snapshot(tmp_path, "2026-11-14", "AAPL", snapshot("AAPL"))
    raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", snapshot("AAPL"))
    raw_store.save_snapshot(tmp_path, "2026-11-16", "MSFT", snapshot("MSFT"))
    # Existing history is deliberately false; status must ignore it.
    (tmp_path / "history.csv").write_text("wrong data", encoding="utf-8")

    run_time = datetime(2026, 11, 16, 9, 30, tzinfo=timezone(timedelta(hours=-6)))
    result = status.build_status(tmp_path, run_time, "2026-11-16", STOCKS, ["AAPL", "MSFT"], [])

    assert set(status.REQUIRED_KEYS).issubset(result)
    assert result["run_time"] == "2026-11-16T15:30:00Z"
    assert result["first_run_date"] == "2026-11-13"
    assert result["days_with_success"] == 3
    assert result["items"] == 2
    assert result["new_today"] == ["MSFT"] and result["gone_today"] == []
    assert result["sources"] == [{"name": "Alpha Vantage", "ok": False, "count": 2}]
    assert isinstance(result["run_time"], str)
    assert isinstance(result["days_with_success"], int)
    assert isinstance(result["items"], int)
    assert isinstance(result["new_today"], list)
    assert isinstance(result["gone_today"], list)
    assert isinstance(result["sources"], list)


def test_first_successful_observation_marks_all_observed_ids_new(tmp_path):
    raw_store.save_snapshot(tmp_path, "2026-11-13", "MSFT", snapshot("MSFT"))
    raw_store.save_snapshot(tmp_path, "2026-11-13", "AAPL", snapshot("AAPL"))
    run_time = datetime(2026, 11, 13, 15, 30, tzinfo=timezone.utc)

    result = status.build_status(tmp_path, run_time, "2026-11-13", STOCKS, ["MSFT", "AAPL"], [])
    assert result["first_run_date"] == "2026-11-13"
    assert result["new_today"] == ["AAPL", "MSFT"]
    assert result["gone_today"] == []


def test_new_stock_appears_once_and_existing_stock_continues(tmp_path):
    raw_store.save_snapshot(tmp_path, "2026-11-13", "AAPL", snapshot("AAPL"))
    raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", snapshot("AAPL"))
    raw_store.save_snapshot(tmp_path, "2026-11-16", "MSFT", snapshot("MSFT"))
    run_time = datetime(2026, 11, 16, 15, 30, tzinfo=timezone.utc)

    result = status.build_status(tmp_path, run_time, "2026-11-16", STOCKS, ["AAPL", "MSFT"], [])
    assert result["new_today"] == ["MSFT"]
    assert result["gone_today"] == []
    assert result["items"] == 2


def test_failed_stock_recovers_without_being_counted_new_again(tmp_path):
    raw_store.save_snapshot(tmp_path, "2026-11-13", "AAPL", snapshot("AAPL"))
    raw_store.save_snapshot(tmp_path, "2026-11-14", "MSFT", snapshot("MSFT"))
    raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", snapshot("AAPL"))
    run_time = datetime(2026, 11, 16, 15, 30, tzinfo=timezone.utc)

    result = status.build_status(tmp_path, run_time, "2026-11-16", STOCKS, ["AAPL"], [])
    assert result["new_today"] == []
    assert result["gone_today"] == []


def test_failed_ticker_does_not_create_disappearance(tmp_path):
    raw_store.save_snapshot(tmp_path, "2026-11-13", "AAPL", snapshot("AAPL"))
    raw_store.save_snapshot(tmp_path, "2026-11-16", "MSFT", snapshot("MSFT"))
    run_time = datetime(2026, 11, 16, 15, 30, tzinfo=timezone.utc)
    failures = [{"item_id": "AAPL", "category": "http_error", "http_status": 403}]

    result = status.build_status(tmp_path, run_time, "2026-11-16", STOCKS, ["MSFT"], failures)
    assert result["new_today"] == ["MSFT"]
    assert result["gone_today"] == []


def test_weekend_repeat_of_friday_bar_has_no_item_movement(tmp_path):
    raw_store.save_snapshot(tmp_path, "2026-11-13", "AAPL", snapshot("AAPL"))
    raw_store.save_snapshot(tmp_path, "2026-11-14", "AAPL", snapshot("AAPL"))
    run_time = datetime(2026, 11, 14, 15, 30, tzinfo=timezone.utc)

    result = status.build_status(tmp_path, run_time, "2026-11-14", STOCKS, ["AAPL"], [])
    assert result["new_today"] == []
    assert result["gone_today"] == []


def test_status_atomic_write_failure_preserves_previous_document(tmp_path, monkeypatch):
    original = {"run_time": "2026-11-13T00:00:00Z", "sources": []}
    path = status.write_status(tmp_path, original)
    previous_bytes = path.read_bytes()

    def failed_replace(*_args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(status.os, "replace", failed_replace)
    with pytest.raises(status.StatusError, match="atomically"):
        status.write_status(tmp_path, {"run_time": "2026-11-16T00:00:00Z", "sources": []})
    assert path.read_bytes() == previous_bytes
    assert sorted(child.name for child in tmp_path.iterdir()) == ["status.json"]


def test_status_rejects_naive_run_time(tmp_path):
    with pytest.raises(status.StatusError, match="UTC offset"):
        status.build_status(tmp_path, datetime(2026, 11, 16), "2026-11-16", STOCKS, [], [])
