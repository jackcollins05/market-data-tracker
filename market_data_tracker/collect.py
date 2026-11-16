"""Explicit, paced daily collection for the configured stock watchlist."""

import argparse
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from market_data_tracker.alpha_vantage import AlphaVantageError, fetch_latest_daily_with_raw
from market_data_tracker.config import REQUEST_INTERVAL_SECONDS, STOCKS, Stock
from market_data_tracker.history import HistoryBuildError, build_history
from market_data_tracker.raw_store import SnapshotError, save_snapshot
from market_data_tracker.status import StatusError, build_status, write_status


def _safe_message(message: str, api_key: str) -> str:
    clean = message.replace(api_key, "[REDACTED]")
    clean = re.sub(r"(?i)(apikey\s*=\s*)[^&\s]+", r"\1[REDACTED]", clean)
    return " ".join(clean.split())[:300]


def _failure(item_id: str, category: str, message: str, api_key: str, http_status=None):
    safe = _safe_message(message, api_key)
    status = " (HTTP {})".format(http_status) if http_status is not None else ""
    print("{}: failed [{}]{}: {}".format(item_id, category, status, safe))
    return {"item_id": item_id, "category": category, "http_status": http_status, "message": safe}


def collect_once(
    data_dir: Path = Path("data"),
    *,
    stocks: Sequence[Stock] = STOCKS,
    fetcher: Callable = fetch_latest_daily_with_raw,
    sleep_fn: Callable = time.sleep,
    clock: Callable = lambda: datetime.now(timezone.utc),
    request_interval_seconds: int = REQUEST_INTERVAL_SECONDS,
) -> int:
    """Attempt each ticker once; return zero only for a complete collection."""
    api_key = os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()
    if not api_key:
        print("Collection failed [missing_api_key]: Set ALPHAVANTAGE_API_KEY before collecting.")
        return 1
    successful_ids = []
    failures = []

    for index, stock in enumerate(stocks):
        if index:
            sleep_fn(request_interval_seconds)
        try:
            record, raw_json = fetcher(stock.item_id)
            if record.item_id != stock.item_id:
                raise SnapshotError("Response ticker does not match the configured stock.")
            observation_date = clock().astimezone(timezone.utc).date().isoformat()
            save_snapshot(data_dir, observation_date, stock.item_id, raw_json)
            successful_ids.append(stock.item_id)
            print("{} ({}): saved (HTTP {}).".format(stock.item_id, stock.name, record.http_status))
        except AlphaVantageError as error:
            failures.append(_failure(stock.item_id, error.category, str(error), api_key, error.http_status))
        except SnapshotError as error:
            category = "storage_error" if str(error).startswith("Could not atomically save") else "snapshot_validation"
            failures.append(_failure(stock.item_id, category, str(error), api_key))
        except Exception:
            # Unexpected exception text could include the credential-bearing request URL.
            failures.append(_failure(stock.item_id, "internal_error", "Unexpected collection error.", api_key))

    if successful_ids:
        try:
            build_history(data_dir)
        except HistoryBuildError as error:
            print("Collection failed [history_error]: {}".format(_safe_message(str(error), api_key)))
            return 1

    try:
        finished_at = clock()
        observation_date = finished_at.astimezone(timezone.utc).date().isoformat()
        payload = build_status(data_dir, finished_at, observation_date, stocks, successful_ids, failures)
        write_status(data_dir, payload)
    except StatusError as error:
        print("Collection failed [status_error]: {}".format(_safe_message(str(error), api_key)))
        return 1

    if not successful_ids:
        print("Collection failed: 0/{} configured stocks saved.".format(len(stocks)))
        return 1
    if failures:
        print("Collection incomplete: {}/{} configured stocks saved.".format(len(successful_ids), len(stocks)))
        return 1
    print("Collection complete: {}/{} configured stocks saved.".format(len(successful_ids), len(stocks)))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Collect the configured stock watchlist once.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Data directory (default: data)")
    args = parser.parse_args(argv)
    return collect_once(args.data_dir)


if __name__ == "__main__":
    raise SystemExit(main())
