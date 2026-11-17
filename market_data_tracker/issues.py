"""Offline daily Issue reports and explicitly authorized GitHub publishing."""

import argparse
import csv
import json
import os
import re
import sys
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from market_data_tracker.config import STOCKS
from market_data_tracker.history import COLUMNS, HistoryBuildError, render_history
from market_data_tracker.status import REQUIRED_KEYS


MARKER = "<!-- market-data-tracker:daily-issue:v1 -->"
TITLE_PREFIX = "[market-data-tracker:daily] "
ISSUE_PATTERN = re.compile(r"^\[market-data-tracker:daily\] (\d{4}-\d{2}-\d{2})$")
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TOP_MOVES = 5
PRIMARY_CRON = "17 22 * * *"
BACKUP_CRON = "31 23 * * *"


class IssueError(ValueError):
    """Daily data or GitHub Issue processing failed safely."""


def _date(value):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise IssueError("Invalid UTC observation date.") from None
    if parsed.isoformat() != value:
        raise IssueError("Invalid UTC observation date.")
    return parsed


def _load(data_dir):
    try:
        status = json.loads((data_dir / "status.json").read_text(encoding="utf-8"))
        with (data_dir / "history.csv").open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != COLUMNS:
                raise IssueError("history.csv has unexpected columns.")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error, json.JSONDecodeError):
        raise IssueError("Cannot read valid history.csv and status.json.") from None
    if not isinstance(status, dict) or not set(REQUIRED_KEYS).issubset(status) or not isinstance(status.get("sources"), list):
        raise IssueError("status.json has an invalid structure.")
    observation = _date(status.get("observation_date_utc"))
    try:
        run_time = datetime.fromisoformat(status["run_time"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, ValueError):
        raise IssueError("status.json has an invalid run_time.") from None
    if run_time.tzinfo is None or run_time.astimezone(timezone.utc).date() != observation:
        raise IssueError("status.json run_time does not match its UTC observation date.")
    seen = set()
    for row in rows:
        row_date = _date(row["date"])
        trading_date = _date(row["trading_date"])
        if trading_date > row_date or not row["item_id"] or (row["date"], row["item_id"]) in seen:
            raise IssueError("history.csv has an invalid or duplicate observation.")
        seen.add((row["date"], row["item_id"]))
        try:
            close = Decimal(row["close"])
        except (InvalidOperation, TypeError):
            raise IssueError("history.csv has an invalid close.") from None
        if not close.is_finite() or close < 0:
            raise IssueError("history.csv has an invalid close.")
    if any(row["date"] > observation.isoformat() for row in rows):
        raise IssueError("history.csv contains observations after the status date.")
    if not any(row["date"] == observation.isoformat() for row in rows):
        raise IssueError("No history rows exist for the status observation date.")
    return status, rows


def check_complete(data_dir, day):
    """Return 0 for verified complete, 1 for absent/incomplete; raise on corrupt data."""
    data_dir = Path(data_dir)
    day = _date(day).isoformat()
    if not (data_dir / "status.json").exists() and not (data_dir / "history.csv").exists():
        return 1
    status, rows = _load(data_dir)
    _verify_raw_history(data_dir)
    if status["observation_date_utc"] != day or not status.get("complete"):
        return 1
    expected = {stock.item_id for stock in STOCKS}
    actual = {row["item_id"] for row in rows if row["date"] == day}
    if actual != expected or status.get("items") != len(expected) or status.get("failures"):
        raise IssueError("Complete status disagrees with the current history rows.")
    if len(status["sources"]) != 1 or status["sources"][0].get("ok") is not True or status["sources"][0].get("count") != len(expected):
        raise IssueError("Complete status disagrees with source success counts.")
    return 0


def _verify_raw_history(data_dir):
    try:
        raw_content, _ = render_history(data_dir)
        disk_content = (Path(data_dir) / "history.csv").read_bytes()
    except (HistoryBuildError, OSError):
        raise IssueError("Cannot verify raw snapshots against history.csv.") from None
    if raw_content != disk_content:
        raise IssueError("history.csv does not match raw snapshots.")


def verify_data(data_dir, day):
    """Validate status date and reproducible history, including partial runs."""
    data_dir = Path(data_dir)
    status, rows = _load(data_dir)
    if status["observation_date_utc"] != _date(day).isoformat():
        raise IssueError("Status observation date does not match the requested UTC date.")
    _verify_raw_history(data_dir)
    if status.get("days_with_success") != len({row["date"] for row in rows}):
        raise IssueError("Status success-day count disagrees with history.csv.")
    latest_items = {row["item_id"] for row in rows if row["date"] == day}
    if status.get("items") != len(latest_items) or status.get("first_run_date") != min(row["date"] for row in rows):
        raise IssueError("Status item count or first date disagrees with history.csv.")
    return status


def decide_action(data_dir, day, now, *, schedule=None, manual_collect=False, quota_confirmed=False):
    """Choose collect/report/skip using a fixed UTC window and verified same-day data."""
    target = _date(day)
    if now.tzinfo is None or now.utcoffset() is None:
        raise IssueError("Schedule time must include a UTC offset.")
    current = now.astimezone(timezone.utc)
    if current.date() != target:
        return "skip"
    minutes = current.hour * 60 + current.minute
    if schedule is not None:
        if schedule == PRIMARY_CRON:
            start = 22 * 60 + 17
        elif schedule == BACKUP_CRON:
            start = 23 * 60 + 31
        else:
            raise IssueError("Unexpected schedule expression.")
        # Leave enough time for 12 paced requests before the UTC date rolls over.
        if minutes < start or minutes >= 23 * 60 + 45:
            return "skip"
    result = check_complete(data_dir, day)
    if result == 0:
        return "report"
    if schedule is None:
        if not manual_collect:
            raise IssueError("Manual report-only run has no complete observation for today.")
        if not quota_confirmed:
            raise IssueError("Manual collection requires quota confirmation.")
    return "collect"


def render_report(data_dir):
    """Render a report from persisted status/history; never fetch market data."""
    status, rows = _load(Path(data_dir))
    day = status["observation_date_utc"]
    dates = sorted({row["date"] for row in rows if row["date"] <= day})
    previous_day = dates[-2] if len(dates) > 1 else None
    latest = {row["item_id"]: row for row in rows if row["date"] == day}
    previous = {}
    for row in sorted(rows, key=lambda item: item["date"]):
        if row["date"] < day:
            previous[row["item_id"]] = row
    sources = status["sources"]
    failures = status.get("failures", [])
    if not isinstance(failures, list):
        raise IssueError("status.json failures must be a list.")
    lines = [MARKER, "# Daily stock observation — {} UTC".format(day), "", "Observation date: **{} UTC**. Source trading dates are shown separately.".format(day), "", "## Source collection", ""]
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("name"), str) or not isinstance(source.get("count"), int):
            raise IssueError("status.json has an invalid source entry.")
        lines.append("- {}: {} ({} successful requests in the latest run).".format(source["name"], "complete" if source.get("ok") is True else "incomplete", source["count"]))
    for failure in failures:
        if not isinstance(failure, dict):
            raise IssueError("status.json has an invalid failure entry.")
        lines.append("- Failed request: {} — {}{}.".format(failure.get("item_id", "unknown"), failure.get("category", "unknown"), " (HTTP {})".format(failure["http_status"]) if failure.get("http_status") is not None else ""))
    if not failures:
        lines.append("- No failed requests recorded in the latest run.")
    lines.extend(["", "## Biggest trading-day closing-price moves", ""])
    if previous_day is None:
        lines.append("First observation: no previous comparison exists.")
    else:
        lines.append("Each stock is compared with its most recent earlier valid observation. Only a newer source trading date counts as a trading-day change.")
        moves = []
        unchanged = []
        uncomparable = []
        for ticker, current in latest.items():
            old = previous.get(ticker)
            if old is None:
                uncomparable.append(ticker)
                continue
            if current["trading_date"] <= old["trading_date"]:
                unchanged.append(ticker)
                continue
            prior = Decimal(old["close"])
            recent = Decimal(current["close"])
            if prior <= 0:
                uncomparable.append(ticker)
                continue
            percent = (recent - prior) / prior * Decimal(100)
            moves.append((abs(percent), ticker, prior, recent, percent, old["trading_date"], current["trading_date"]))
        moves.sort(key=lambda move: (-move[0], move[1]))
        if moves:
            lines.extend(["| Stock | Previous close | Latest close | Change | Trading dates |", "| --- | ---: | ---: | ---: | --- |"])
            for _, ticker, prior, recent, percent, old_trade, new_trade in moves[:TOP_MOVES]:
                lines.append("| {} | {} | {} | {:+.2f}% | {} → {} |".format(ticker, prior, recent, percent, old_trade, new_trade))
            lines.append("")
            lines.append("Rule: rank the five largest absolute percentage changes among stocks with a newer trading date; signed change = (latest close − previous close) ÷ previous close × 100. Ties use ticker order.")
        else:
            lines.append("No new trading-day closing-price changes were observed. This may be a market-closed day or a day before the source published a new bar.")
        if unchanged:
            lines.append("Unchanged source trading date (market closed or no new bar): {}.".format(", ".join(sorted(unchanged))))
        if uncomparable:
            lines.append("No valid previous close for: {}.".format(", ".join(sorted(uncomparable))))
    new = status.get("new_today", [])
    gone = status.get("gone_today", [])
    if not isinstance(new, list) or not isinstance(gone, list):
        raise IssueError("status.json item changes must be lists.")
    failed_ids = {failure.get("item_id") for failure in failures}
    if failed_ids.intersection(gone):
        raise IssueError("A failed request cannot confirm a disappeared stock.")
    lines.extend(["", "## Item changes", "", "- Newly observed by this project: {}.".format(", ".join(new) if new else "none"), "- Confirmed disappeared: {}.".format(", ".join(gone) if gone else "none"), "- Failed requests are not evidence of disappearance.", "", "Seven-day progress: **{}/7** distinct UTC dates with valid snapshots.".format(status.get("days_with_success", 0))])
    return TITLE_PREFIX + day, "\n".join(lines) + "\n"


class GitHubClient:
    def __init__(self, repo, token, opener=urlopen):
        if not REPO_PATTERN.fullmatch(repo):
            raise IssueError("Repository must be owner/name.")
        self.base = "https://api.github.com/repos/{}/issues".format(repo)
        self.token = token
        self.opener = opener

    def request(self, method, url, payload=None):
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json", "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"}
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            raise IssueError("GitHub API {} failed (HTTP {}). Check repository permissions, token access, or rate limit.".format(method, error.code)) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise IssueError("GitHub API {} failed. Check network access and API availability.".format(method)) from None

    def project_issues(self):
        found = []
        for page in range(1, 51):
            batch = self.request("GET", self.base + "?state=all&per_page=100&page={}".format(page))
            if not isinstance(batch, list):
                raise IssueError("GitHub returned an invalid Issues list.")
            found.extend(issue for issue in batch if isinstance(issue, dict) and "pull_request" not in issue and isinstance(issue.get("title"), str) and ISSUE_PATTERN.fullmatch(issue["title"]) and MARKER in (issue.get("body") or ""))
            if len(batch) < 100:
                return found
        raise IssueError("GitHub Issues list exceeds the safety page limit.")


def publish_report(title, body, repo, token, opener=urlopen):
    """Update only identified project Issues, then close the prior calendar day's Issue."""
    client = GitHubClient(repo, token, opener)
    day = _date(title[len(TITLE_PREFIX):])
    issues = client.project_issues()
    today = [issue for issue in issues if issue["title"] == title]
    if len(today) > 1:
        raise IssueError("Multiple identified project Issues already exist for this UTC date.")
    if today:
        issue = today[0]
        client.request("PATCH", client.base + "/{}".format(issue["number"]), {"title": title, "body": body, "state": "open"})
        action = "updated"
    else:
        client.request("POST", client.base, {"title": title, "body": body})
        action = "created"
    yesterday = (day - timedelta(days=1)).isoformat()
    for issue in issues:
        if issue["title"] == TITLE_PREFIX + yesterday and issue.get("state") == "open":
            client.request("PATCH", client.base + "/{}".format(issue["number"]), {"state": "closed"})
    return action


def main(argv=None):
    parser = argparse.ArgumentParser(description="Preview or explicitly publish the UTC daily GitHub Issue.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--publish", action="store_true", help="Modify GitHub Issues (requires GITHUB_TOKEN).")
    parser.add_argument("--repo", help="owner/name; defaults to GITHUB_REPOSITORY when publishing")
    parser.add_argument("--check-complete", action="store_true", help="Exit 0 for a verified complete UTC date, 1 if absent/incomplete, 2 if invalid.")
    parser.add_argument("--verify-data", action="store_true", help="Verify status date and history against raw snapshots without network access.")
    parser.add_argument("--gate", action="store_true", help="Print collect, report, or skip for a UTC date without network access.")
    parser.add_argument("--schedule", choices=(PRIMARY_CRON, BACKUP_CRON), help="Scheduled cron expression for --gate; omit for manual recovery.")
    parser.add_argument("--manual-collect", action="store_true", help="Permit manual collection in --gate.")
    parser.add_argument("--quota-confirmed", action="store_true", help="Acknowledge manual quota check in --gate.")
    parser.add_argument("--date", help="UTC date for --check-complete; defaults to current UTC date")
    args = parser.parse_args(argv)
    try:
        if args.gate:
            now = datetime.now(timezone.utc)
            print(decide_action(args.data_dir, args.date or now.date().isoformat(), now, schedule=args.schedule, manual_collect=args.manual_collect, quota_confirmed=args.quota_confirmed))
            return 0
        if args.check_complete:
            result = check_complete(args.data_dir, args.date or datetime.now(timezone.utc).date().isoformat())
            print("Complete UTC collection verified." if result == 0 else "No complete UTC collection for this date.")
            return result
        if args.verify_data:
            verify_data(args.data_dir, args.date or datetime.now(timezone.utc).date().isoformat())
            print("UTC status and reproducible history verified.")
            return 0
        title, body = render_report(args.data_dir)
        if not args.publish:
            print(title + "\n" + body, end="")
            return 0
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if not token:
            raise IssueError("GITHUB_TOKEN is required for publishing.")
        repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
        action = publish_report(title, body, repo, token)
        print("Daily project Issue {} for {}.".format(action, title[len(TITLE_PREFIX):]))
        return 0
    except IssueError as error:
        print("Daily Issue error: {}".format(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
