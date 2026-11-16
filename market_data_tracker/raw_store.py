"""Validate and atomically store original Alpha Vantage JSON snapshots."""

import gzip
import json
import os
import re
import tempfile
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from market_data_tracker.alpha_vantage import (
    SERIES_KEY,
    SYMBOL_PATTERN,
    AlphaVantageError,
    DailyRecord,
    normalize_daily,
)


MAX_SNAPSHOT_BYTES = 10 * 1024 * 1024


class SnapshotError(ValueError):
    """A snapshot cannot be validated, read, or safely saved."""


class _DuplicateJSONKey(ValueError):
    """A JSON object repeated a key, so its values are ambiguous."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey()
        result[key] = value
    return result


def _reject_non_json_constant(_value: str) -> None:
    raise ValueError("Non-JSON numeric constant")


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise SnapshotError("Observation date must be YYYY-MM-DD.") from None
    if parsed.isoformat() != value:
        raise SnapshotError("Observation date must be YYYY-MM-DD.")
    return parsed


def _symbol(value: str) -> str:
    if not isinstance(value, str) or not SYMBOL_PATTERN.fullmatch(value):
        raise SnapshotError("Snapshot ticker is invalid.")
    return value.upper()


def _contains_credential(value: Any) -> bool:
    """Reject recognizable credential fields before preserving a full response."""
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and re.search(r"api.?key|password|secret|token|authorization", key, re.I):
                return True
            if _contains_credential(child):
                return True
    elif isinstance(value, list):
        return any(_contains_credential(child) for child in value)
    elif isinstance(value, str):
        return bool(re.search(r"(?:api.?key|password|secret|token)\s*[=:]\s*\S+", value, re.I))
    return False


def parse_snapshot(raw_json: bytes, symbol: str, observation_date: str) -> DailyRecord:
    """Select the newest trading bar on or before the UTC observation date."""
    observed = _date(observation_date)
    ticker = _symbol(symbol)
    if not isinstance(raw_json, bytes):
        raise SnapshotError("Snapshot must contain original JSON bytes.")
    if len(raw_json) > MAX_SNAPSHOT_BYTES:
        raise SnapshotError("Snapshot exceeds the supported size limit.")
    try:
        payload = json.loads(
            raw_json,
            parse_constant=_reject_non_json_constant,
            object_pairs_hook=_unique_object,
        )
    except _DuplicateJSONKey:
        raise SnapshotError("Snapshot contains duplicate JSON object keys.") from None
    except (ValueError, UnicodeError):
        raise SnapshotError("Snapshot contains invalid JSON.") from None
    if not isinstance(payload, dict):
        raise SnapshotError("Snapshot JSON root must be an object.")
    if _contains_credential(payload):
        raise SnapshotError("Snapshot contains a credential-like field or value.")

    series = payload.get(SERIES_KEY)
    if not isinstance(series, dict) or not series:
        raise SnapshotError("Snapshot has no daily time series.")
    eligible = {}
    for trading_day, bar in series.items():
        try:
            parsed_day = _date(trading_day)
        except SnapshotError:
            raise SnapshotError("Snapshot has an invalid trading date.") from None
        if parsed_day <= observed:
            eligible[trading_day] = bar
    if not eligible:
        raise SnapshotError("Snapshot has no trading date on or before its observation date.")

    selected_payload = dict(payload)
    selected_payload[SERIES_KEY] = eligible
    try:
        record = normalize_daily(ticker, selected_payload, 200, api_key="")
    except AlphaVantageError as error:
        # Do not copy vendor error text into logs; it could contain a credential.
        raise SnapshotError("Invalid Alpha Vantage snapshot ({}).".format(error.category)) from None
    return replace(record, observation_date_utc=observation_date)


def save_snapshot(
    data_dir: Path,
    observation_date: str,
    symbol: str,
    raw_json: bytes,
) -> Path:
    """Validate first, then atomically replace only this date and ticker's gzip file."""
    parse_snapshot(raw_json, symbol, observation_date)
    ticker = _symbol(symbol)
    target = Path(data_dir) / "raw" / observation_date / "alphavantage_{}.json.gz".format(ticker)
    if target.exists() and _date(observation_date) != _utc_today():
        raise SnapshotError("Previous-date snapshots are immutable.")
    temporary = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".snapshot-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            with gzip.GzipFile(filename="", fileobj=output, mode="wb", mtime=0) as compressed:
                compressed.write(raw_json)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    except OSError:
        raise SnapshotError("Could not atomically save snapshot.") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target
