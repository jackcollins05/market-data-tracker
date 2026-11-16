"""Synthetic, offline tests for reproducible history reconstruction."""

import csv
import gzip
import io
import json
from datetime import date
from pathlib import Path

import pytest

from market_data_tracker import alpha_vantage, history, raw_store


def response(symbol, bars):
    """Build synthetic TIME_SERIES_DAILY JSON from date-to-(close, volume) pairs."""
    series = {}
    for trading_date, (close, volume) in bars.items():
        series[trading_date] = {
            "1. open": "101.2500",
            "2. high": "105.0000",
            "3. low": "100.0000",
            "4. close": close,
            "5. volume": volume,
        }
    return json.dumps(
        {"Meta Data": {"2. Symbol": symbol}, "Time Series (Daily)": series},
        separators=(",", ":"),
    ).encode("utf-8")


def save(data_dir, observed, symbol, bars):
    return raw_store.save_snapshot(data_dir, observed, symbol, response(symbol, bars))


def read_rows(content):
    return list(csv.DictReader(io.StringIO(content.decode("utf-8"))))


def test_multiple_stocks_dates_column_order_sorting_and_numeric_format(tmp_path):
    save(tmp_path, "2026-11-16", "MSFT", {"2026-11-13": ("104.5000", "25")})
    save(tmp_path, "2026-11-13", "AAPL", {"2026-11-13": ("103.7500", "123456")})
    save(tmp_path, "2026-11-16", "AAPL", {"2026-11-13": ("103.7500", "123456")})

    path, count = history.build_history(tmp_path)
    content = path.read_bytes()
    assert count == 3
    assert content.startswith(b"date,item_id,item,source,open,high,low,close,volume,trading_date\n")
    assert b"\r\n" not in content
    rows = read_rows(content)
    assert [(row["date"], row["item_id"]) for row in rows] == [
        ("2026-11-13", "AAPL"),
        ("2026-11-16", "AAPL"),
        ("2026-11-16", "MSFT"),
    ]
    assert rows[1] == {
        "date": "2026-11-16",
        "item_id": "AAPL",
        "item": "AAPL",
        "source": "Alpha Vantage",
        "open": "101.2500",
        "high": "105.0000",
        "low": "100.0000",
        "close": "103.7500",
        "volume": "123456",
        "trading_date": "2026-11-13",
    }


@pytest.mark.parametrize(
    ("observed", "prior_trading_date", "later_trading_date"),
    [("2026-11-14", "2026-11-13", "2026-11-16"), ("2026-11-26", "2026-11-25", "2026-11-27")],
)
def test_weekend_or_holiday_uses_latest_eligible_trading_date(
    tmp_path, observed, prior_trading_date, later_trading_date
):
    raw = response(
        "AAPL",
        {prior_trading_date: ("103.7500", "123456"), later_trading_date: ("104.0000", "456")},
    )
    raw_store.save_snapshot(tmp_path, observed, "AAPL", raw)
    content, count = history.render_history(tmp_path)
    row = read_rows(content)[0]

    assert count == 1
    assert row["date"] == observed
    assert row["trading_date"] == prior_trading_date
    assert row["close"] == "103.7500"


def test_missing_source_on_a_day_creates_no_disappearance_row(tmp_path):
    save(tmp_path, "2026-11-13", "AAPL", {"2026-11-13": ("103.7500", "100")})
    save(tmp_path, "2026-11-13", "MSFT", {"2026-11-13": ("104.5000", "200")})
    save(tmp_path, "2026-11-16", "AAPL", {"2026-11-13": ("103.7500", "100")})

    content, count = history.render_history(tmp_path)
    assert count == 3
    assert [(row["date"], row["item_id"]) for row in read_rows(content)] == [
        ("2026-11-13", "AAPL"),
        ("2026-11-13", "MSFT"),
        ("2026-11-16", "AAPL"),
    ]


def test_same_day_rerun_does_not_duplicate_history(tmp_path, monkeypatch):
    monkeypatch.setattr(raw_store, "_utc_today", lambda: date(2026, 11, 16))
    save(tmp_path, "2026-11-16", "AAPL", {"2026-11-13": ("103.7500", "100")})
    save(tmp_path, "2026-11-16", "AAPL", {"2026-11-13": ("104.0000", "200")})
    content, count = history.render_history(tmp_path)
    assert count == 1
    assert read_rows(content)[0]["close"] == "104.0000"


def test_rebuild_twice_and_after_deletion_has_identical_bytes(tmp_path, monkeypatch):
    save(
        tmp_path,
        "2026-11-16",
        "AAPL",
        {"2026-11-12": ("102.0000", "100000"), "2026-11-13": ("103.7500", "123456")},
    )
    path, _ = history.build_history(tmp_path)
    first = path.read_bytes()

    class DifferentClock:
        @staticmethod
        def now(_timezone):
            class Moment:
                @staticmethod
                def date():
                    from datetime import date

                    return date(2099, 1, 1)

            return Moment()

    monkeypatch.setattr(alpha_vantage, "datetime", DifferentClock)
    history.build_history(tmp_path)
    assert path.read_bytes() == first
    path.unlink()
    history.build_history(tmp_path)
    assert path.read_bytes() == first


def test_duplicate_key_in_stored_snapshot_blocks_rebuild_and_preserves_history(tmp_path):
    save(tmp_path, "2026-11-13", "AAPL", {"2026-11-13": ("103.7500", "100")})
    path, _ = history.build_history(tmp_path)
    good_history = path.read_bytes()

    payload = json.loads(response("MSFT", {"2026-11-13": ("104.5000", "200")}))
    bar = json.dumps(payload["Time Series (Daily)"]["2026-11-13"], separators=(",", ":"))
    duplicate_dates = (
        '{"Meta Data":{"2. Symbol":"MSFT"},"Time Series (Daily)":{'
        '"2026-11-13":' + bar + ',"2026-11-13":' + bar + '}}'
    ).encode("utf-8")
    corrupt = tmp_path / "raw" / "2026-11-16" / "alphavantage_MSFT.json.gz"
    corrupt.parent.mkdir(parents=True)
    with gzip.open(corrupt, "wb") as output:
        output.write(duplicate_dates)

    with pytest.raises(history.HistoryBuildError, match="duplicate JSON object keys"):
        history.build_history(tmp_path)
    assert path.read_bytes() == good_history


@pytest.mark.parametrize(("raw_content", "fragment"), [(b"not gzip", "decompress"), (b"{broken", "invalid JSON")])
def test_corrupt_gzip_or_invalid_json_blocks_partial_rebuild(tmp_path, raw_content, fragment):
    save(tmp_path, "2026-11-13", "AAPL", {"2026-11-13": ("103.7500", "100")})
    path, _ = history.build_history(tmp_path)
    good_history = path.read_bytes()
    broken = tmp_path / "raw" / "2026-11-16" / "alphavantage_MSFT.json.gz"
    broken.parent.mkdir(parents=True)
    if fragment == "decompress":
        broken.write_bytes(raw_content)
    else:
        with gzip.open(broken, "wb") as output:
            output.write(raw_content)

    with pytest.raises(history.HistoryBuildError, match=fragment):
        history.build_history(tmp_path)
    assert path.read_bytes() == good_history


def test_failed_history_atomic_replace_preserves_previous_file(tmp_path, monkeypatch):
    save(tmp_path, "2026-11-16", "AAPL", {"2026-11-13": ("103.7500", "100")})
    path, _ = history.build_history(tmp_path)
    good_history = path.read_bytes()

    def failed_replace(*_args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(history.os, "replace", failed_replace)
    with pytest.raises(history.HistoryBuildError, match="atomically"):
        history.build_history(tmp_path)
    assert path.read_bytes() == good_history
    assert sorted(child.name for child in tmp_path.iterdir()) == ["history.csv", "raw"]


def test_missing_raw_directory_does_not_create_history(tmp_path):
    with pytest.raises(history.HistoryBuildError, match="does not exist"):
        history.build_history(tmp_path)
    assert not (tmp_path / "history.csv").exists()


def test_cli_rebuild_uses_only_temporary_data_and_no_key(tmp_path, monkeypatch, capsys):
    save(tmp_path, "2026-11-16", "AAPL", {"2026-11-13": ("103.7500", "100")})

    def forbidden_request(*_args, **_kwargs):
        pytest.fail("History rebuild must not make a network request")

    monkeypatch.setattr(alpha_vantage, "urlopen", forbidden_request)
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "synthetic-secret-must-not-appear")
    project_data = Path(__file__).resolve().parents[1] / "data"
    before = project_data.exists()
    assert history.main(["--data-dir", str(tmp_path)]) == 0

    assert (tmp_path / "history.csv").exists()
    assert project_data.exists() == before
    assert "synthetic-secret-must-not-appear" not in capsys.readouterr().out
