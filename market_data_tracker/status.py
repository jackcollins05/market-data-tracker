"""Summarize raw snapshot coverage and atomically write collection status."""

import csv
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Sequence

from market_data_tracker.config import SOURCE_NAME, Stock
from market_data_tracker.history import HistoryBuildError, render_history


REQUIRED_KEYS = (
    "run_time",
    "first_run_date",
    "days_with_success",
    "items",
    "new_today",
    "gone_today",
    "sources",
)


class StatusError(ValueError):
    """A safe status report cannot be computed or written."""


def build_status(
    data_dir: Path,
    run_time: datetime,
    observation_date: str,
    stocks: Sequence[Stock],
    successful_ids: Sequence[str],
    failures: Sequence[Dict[str, object]],
) -> Dict[str, object]:
    """Derive coverage from raw data, never from an existing history.csv."""
    if run_time.tzinfo is None or run_time.utcoffset() is None:
        raise StatusError("Run time must include a UTC offset.")
    utc_time = run_time.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    raw_dir = Path(data_dir) / "raw"
    if raw_dir.exists():
        try:
            content, _count = render_history(data_dir)
        except HistoryBuildError as error:
            raise StatusError("Cannot summarize raw snapshots: {}".format(error)) from None
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))
    else:
        rows = []

    dates = sorted({row["date"] for row in rows})
    latest_date = dates[-1] if dates else None
    latest_items = {row["item_id"] for row in rows if row["date"] == latest_date}
    today_items = {row["item_id"] for row in rows if row["date"] == observation_date}
    previously_observed = {row["item_id"] for row in rows if row["date"] < observation_date}
    complete = len(successful_ids) == len(stocks) and not failures

    # A failed per-ticker request is not evidence that a stock disappeared.
    # The per-ticker API cannot establish genuine disappearance.
    status = {
        "run_time": utc_time,
        "first_run_date": dates[0] if dates else None,
        "days_with_success": len(dates),
        "items": len(latest_items),
        "new_today": sorted(today_items - previously_observed),
        "gone_today": [],
        "sources": [{"name": SOURCE_NAME, "ok": complete, "count": len(successful_ids)}],
        "run_ok": bool(successful_ids),
        "complete": complete,
        "observation_date_utc": observation_date,
        "failures": list(failures),
    }
    return status


def write_status(data_dir: Path, payload: Dict[str, object]) -> Path:
    """Replace status.json only after the whole document is safely written."""
    target = Path(data_dir) / "status.json"
    content = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    temporary = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".status-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    except OSError:
        raise StatusError("Could not atomically replace status.json.") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target
