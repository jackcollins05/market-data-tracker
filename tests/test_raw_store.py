"""Synthetic, offline tests for atomic raw snapshot storage."""

import gzip
import json
from datetime import date
from pathlib import Path

import pytest

from market_data_tracker import raw_store


def response(symbol="AAPL", close="103.7500", trading_date="2026-11-13"):
    """A synthetic source response, never a production observation."""
    payload = {
        "Meta Data": {"1. Information": "Daily Prices", "2. Symbol": symbol},
        "Time Series (Daily)": {
            trading_date: {
                "1. open": "101.2500",
                "2. high": "104.5000",
                "3. low": "100.0000",
                "4. close": close,
                "5. volume": "123456",
            }
        },
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def duplicate_response(kind):
    """Synthetic JSON with a duplicate key that dict-based fixtures cannot express."""
    payload = json.loads(response())
    metadata = json.dumps(payload["Meta Data"], separators=(",", ":"))
    bar = json.dumps(payload["Time Series (Daily)"]["2026-11-13"], separators=(",", ":"))
    if kind == "ohlcv":
        bar = bar.replace('"4. close":"103.7500"', '"4. close":"103.7500","4. close":"102.0000"')
    series = '"2026-11-13":{}'.format(bar)
    if kind == "trading_date":
        series += ',"2026-11-13":{}'.format(bar)
    extra = ',"Extra":{"nested":{"value":1,"value":2}}' if kind == "nested" else ""
    return '{{"Meta Data":{},"Time Series (Daily)":{{{}}}{}}}'.format(metadata, series, extra).encode()


@pytest.mark.parametrize("kind", ["trading_date", "ohlcv", "nested"])
def test_duplicate_json_keys_at_any_depth_are_rejected_before_storage(tmp_path, kind):
    with pytest.raises(raw_store.SnapshotError, match="duplicate JSON object keys") as caught:
        raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", duplicate_response(kind))
    assert "2026-11-13" not in str(caught.value)
    assert "4. close" not in str(caught.value)
    assert not (tmp_path / "raw").exists()


def test_distinct_trading_dates_remain_valid_and_raw_bytes_are_preserved(tmp_path):
    payload = json.loads(response())
    payload["Time Series (Daily)"]["2026-11-12"] = payload["Time Series (Daily)"]["2026-11-13"].copy()
    original = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    path = raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", original)

    with gzip.open(path, "rb") as compressed:
        assert compressed.read() == original
    assert raw_store.parse_snapshot(original, "AAPL", "2026-11-16").trading_date == "2026-11-13"


def test_duplicate_key_replacement_preserves_existing_good_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(raw_store, "_utc_today", lambda: date(2026, 11, 16))
    path = raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", response())
    before = path.read_bytes()

    with pytest.raises(raw_store.SnapshotError, match="duplicate JSON object keys"):
        raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", duplicate_response("trading_date"))
    assert path.read_bytes() == before
    assert sorted(child.name for child in path.parent.iterdir()) == [path.name]


def test_gzip_storage_preserves_original_json_and_uses_expected_filename(tmp_path):
    original = response()
    path = raw_store.save_snapshot(tmp_path, "2026-11-16", "aapl", original)

    assert path == tmp_path / "raw" / "2026-11-16" / "alphavantage_AAPL.json.gz"
    assert path.read_bytes().startswith(b"\x1f\x8b")
    with gzip.open(path, "rb") as compressed:
        assert compressed.read() == original
    assert raw_store.parse_snapshot(original, "AAPL", "2026-11-16").close == 103.75


def test_multiple_stocks_have_separate_files_and_previous_date_is_untouched(tmp_path):
    earlier = raw_store.save_snapshot(tmp_path, "2026-11-13", "AAPL", response())
    earlier_bytes = earlier.read_bytes()
    apple = raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", response(close="102.0000"))
    microsoft = raw_store.save_snapshot(tmp_path, "2026-11-16", "MSFT", response(symbol="MSFT"))

    assert apple != microsoft
    assert earlier.read_bytes() == earlier_bytes
    assert sorted(path.name for path in apple.parent.iterdir()) == [
        "alphavantage_AAPL.json.gz",
        "alphavantage_MSFT.json.gz",
    ]


def test_same_day_rerun_replaces_only_its_own_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(raw_store, "_utc_today", lambda: date(2026, 11, 16))
    first = raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", response(close="103.7500"))
    other = raw_store.save_snapshot(tmp_path, "2026-11-16", "MSFT", response(symbol="MSFT"))
    other_bytes = other.read_bytes()
    updated = response(close="102.0000")

    assert raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", updated) == first
    with gzip.open(first, "rb") as compressed:
        assert compressed.read() == updated
    assert other.read_bytes() == other_bytes
    assert len(list(first.parent.glob("*.json.gz"))) == 2


def test_failed_validation_preserves_existing_snapshot(tmp_path):
    path = raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", response())
    good_bytes = path.read_bytes()
    invalid = json.loads(response())
    del invalid["Time Series (Daily)"]["2026-11-13"]["4. close"]

    with pytest.raises(raw_store.SnapshotError, match="invalid_data"):
        raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", json.dumps(invalid).encode())
    assert path.read_bytes() == good_bytes
    assert sorted(child.name for child in path.parent.iterdir()) == [path.name]


def test_previous_date_snapshot_cannot_be_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr(raw_store, "_utc_today", lambda: date(2026, 11, 16))
    path = raw_store.save_snapshot(tmp_path, "2026-11-13", "AAPL", response())
    good_bytes = path.read_bytes()
    with pytest.raises(raw_store.SnapshotError, match="immutable"):
        raw_store.save_snapshot(tmp_path, "2026-11-13", "AAPL", response(close="102.0000"))
    assert path.read_bytes() == good_bytes


def test_failed_atomic_replace_preserves_existing_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(raw_store, "_utc_today", lambda: date(2026, 11, 16))
    path = raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", response())
    good_bytes = path.read_bytes()

    def failed_replace(*_args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(raw_store.os, "replace", failed_replace)
    with pytest.raises(raw_store.SnapshotError, match="atomically"):
        raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", response(close="102.0000"))
    assert path.read_bytes() == good_bytes
    assert sorted(child.name for child in path.parent.iterdir()) == [path.name]


@pytest.mark.parametrize("invalid", [b"{broken", b"[]", b'{"value":NaN}'])
def test_invalid_json_or_shape_is_not_stored(tmp_path, invalid):
    with pytest.raises(raw_store.SnapshotError):
        raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", invalid)
    assert not (tmp_path / "raw").exists()


def test_credential_like_content_is_rejected_without_writing(tmp_path):
    payload = json.loads(response())
    payload["api_key"] = "synthetic-secret-that-must-not-be-stored"
    with pytest.raises(raw_store.SnapshotError, match="credential") as caught:
        raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", json.dumps(payload).encode())
    assert "synthetic-secret-that-must-not-be-stored" not in str(caught.value)
    assert not (tmp_path / "raw").exists()


def test_oversized_snapshot_is_rejected_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(raw_store, "MAX_SNAPSHOT_BYTES", 10)
    with pytest.raises(raw_store.SnapshotError, match="size limit"):
        raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", response())
    assert not (tmp_path / "raw").exists()


def test_tests_do_not_create_production_data(tmp_path):
    project_data = Path(__file__).resolve().parents[1] / "data"
    before = project_data.exists()
    raw_store.save_snapshot(tmp_path, "2026-11-16", "AAPL", response())
    assert project_data.exists() == before
