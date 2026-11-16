"""Rebuild deterministic history.csv exclusively from dated raw snapshots."""

import argparse
import csv
import gzip
import io
import os
import tempfile
import zlib
from datetime import date
from pathlib import Path
from typing import List, Tuple

from market_data_tracker.alpha_vantage import SYMBOL_PATTERN
from market_data_tracker.raw_store import MAX_SNAPSHOT_BYTES, SnapshotError, parse_snapshot


COLUMNS = ("date", "item_id", "item", "source", "open", "high", "low", "close", "volume", "trading_date")
class HistoryBuildError(ValueError):
    """Raw snapshots cannot be turned into a complete history."""


def _observation_date(folder: Path) -> str:
    try:
        parsed = date.fromisoformat(folder.name)
    except ValueError:
        raise HistoryBuildError("Invalid raw snapshot date directory: {}".format(folder)) from None
    if parsed.isoformat() != folder.name:
        raise HistoryBuildError("Invalid raw snapshot date directory: {}".format(folder))
    return folder.name


def _ticker(path: Path) -> str:
    prefix, suffix = "alphavantage_", ".json.gz"
    if not path.name.startswith(prefix) or not path.name.endswith(suffix):
        raise HistoryBuildError("Unexpected raw snapshot filename: {}".format(path))
    ticker = path.name[len(prefix) : -len(suffix)]
    if ticker != ticker.upper() or not SYMBOL_PATTERN.fullmatch(ticker):
        raise HistoryBuildError("Invalid raw snapshot ticker filename: {}".format(path))
    return ticker


def render_history(data_dir: Path) -> Tuple[bytes, int]:
    """Read every raw snapshot and render stable UTF-8 CSV bytes in memory."""
    raw_dir = Path(data_dir) / "raw"
    if not raw_dir.is_dir():
        raise HistoryBuildError("Raw snapshot directory does not exist: {}".format(raw_dir))

    rows: List[Tuple[str, ...]] = []
    seen = set()
    for folder in sorted(raw_dir.iterdir()):
        if not folder.is_dir():
            raise HistoryBuildError("Unexpected file in raw snapshot directory: {}".format(folder))
        observation_date = _observation_date(folder)
        for path in sorted(folder.iterdir()):
            if not path.is_file():
                raise HistoryBuildError("Unexpected directory in raw snapshot date: {}".format(path))
            ticker = _ticker(path)
            identity = (observation_date, ticker)
            if identity in seen:
                raise HistoryBuildError("Duplicate raw snapshot for {} on {}.".format(ticker, observation_date))
            seen.add(identity)
            try:
                with gzip.open(path, "rb") as compressed:
                    raw_json = compressed.read(MAX_SNAPSHOT_BYTES + 1)
            except (OSError, EOFError, zlib.error):
                raise HistoryBuildError("Cannot decompress raw snapshot: {}".format(path)) from None
            if len(raw_json) > MAX_SNAPSHOT_BYTES:
                raise HistoryBuildError("Raw snapshot exceeds size limit: {}".format(path))
            try:
                record = parse_snapshot(raw_json, ticker, observation_date)
            except SnapshotError as error:
                raise HistoryBuildError("Invalid raw snapshot {}: {}".format(path, error)) from None
            rows.append(
                (
                    observation_date,
                    record.item_id,
                    record.item,
                    record.source,
                    format(record.open, "f"),
                    format(record.high, "f"),
                    format(record.low, "f"),
                    format(record.close, "f"),
                    str(record.volume),
                    record.trading_date,
                )
            )

    if not rows:
        raise HistoryBuildError("No raw snapshots found; existing history was not changed.")
    rows.sort(key=lambda row: (row[0], row[1]))
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8"), len(rows)


def build_history(data_dir: Path) -> Tuple[Path, int]:
    """Render completely before atomically replacing history.csv."""
    content, row_count = render_history(data_dir)
    target = Path(data_dir) / "history.csv"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".history-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    except OSError:
        raise HistoryBuildError("Could not atomically replace history.csv.") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target, row_count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild history.csv from raw snapshots only.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Data directory (default: data)")
    args = parser.parse_args(argv)
    try:
        path, row_count = build_history(args.data_dir)
    except HistoryBuildError as error:
        print("History rebuild failed: {}".format(error))
        return 1
    print("Rebuilt {} with {} rows.".format(path, row_count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
