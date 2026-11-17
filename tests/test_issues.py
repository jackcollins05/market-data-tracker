"""Synthetic daily data and mocked GitHub REST responses; no live requests."""

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError

import pytest

from market_data_tracker import issues
from market_data_tracker.config import STOCKS
from market_data_tracker.history import COLUMNS


DAY = "2026-11-16"
YESTERDAY = "2026-11-15"


def row(day, ticker, close, trading_date=None):
    return [day, ticker, ticker, "Alpha Vantage", close, close, close, close, "100", trading_date or day]


def data(tmp_path, rows, *, day=DAY, new=None, gone=None, failures=None, complete=True, count=None):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    (tmp_path / "history.csv").write_text(stream.getvalue(), encoding="utf-8")
    current = [entry for entry in rows if entry[0] == day]
    status = {
        "run_time": day + "T23:00:00Z",
        "first_run_date": min(entry[0] for entry in rows) if rows else None,
        "observation_date_utc": day,
        "sources": [{"name": "Alpha Vantage", "ok": complete, "count": len(current) if count is None else count}],
        "failures": failures or [],
        "complete": complete,
        "items": len(current),
        "new_today": new or [],
        "gone_today": gone or [],
        "days_with_success": len({entry[0] for entry in rows}),
    }
    (tmp_path / "status.json").write_text(json.dumps(status), encoding="utf-8")
    return status


def project_issue(number, day, state="open", *, marker=True):
    return {"number": number, "title": issues.TITLE_PREFIX + day, "body": issues.MARKER if marker else "unrelated", "state": state}


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return json.dumps(self.value).encode()


class FakeGitHub:
    def __init__(self, listed=None, error=None):
        self.listed = listed or []
        self.error = error
        self.calls = []

    def __call__(self, request, timeout):
        assert timeout == 20
        payload = json.loads(request.data) if request.data else None
        self.calls.append((request.get_method(), request.full_url, payload))
        if self.error:
            raise HTTPError(request.full_url, self.error, "synthetic secret in response", {}, None)
        if request.get_method() == "GET":
            return Response(self.listed)
        return Response({"number": 10})


def test_first_observation_has_no_previous_comparison(tmp_path):
    data(tmp_path, [row(DAY, "AAPL", "100")], new=["AAPL"])
    title, body = issues.render_report(tmp_path)
    assert title == issues.TITLE_PREFIX + DAY
    assert "First observation: no previous comparison exists" in body
    assert "Newly observed by this project: AAPL" in body


def test_positive_and_negative_movements_rank_by_absolute_percentage(tmp_path):
    data(tmp_path, [row(YESTERDAY, "AAPL", "100"), row(YESTERDAY, "MSFT", "200"), row(DAY, "AAPL", "110"), row(DAY, "MSFT", "160")])
    _, body = issues.render_report(tmp_path)
    assert "| MSFT | 200 | 160 | -20.00% |" in body
    assert "| AAPL | 100 | 110 | +10.00% |" in body
    assert body.index("| MSFT |") < body.index("| AAPL |")
    assert "(latest close − previous close) ÷ previous close × 100" in body


def test_zero_or_missing_previous_close_is_not_divided_by(tmp_path):
    data(tmp_path, [row(YESTERDAY, "AAPL", "0"), row(DAY, "AAPL", "110"), row(DAY, "MSFT", "50")])
    _, body = issues.render_report(tmp_path)
    assert "No valid previous close for: AAPL, MSFT" in body
    assert "No new trading-day closing-price changes" in body


def test_unchanged_weekend_bar_has_no_trading_change(tmp_path):
    data(tmp_path, [row(YESTERDAY, "AAPL", "100", "2026-11-13"), row(DAY, "AAPL", "100", "2026-11-13")])
    _, body = issues.render_report(tmp_path)
    assert "Unchanged source trading date" in body
    assert "No new trading-day closing-price changes" in body
    assert "+0.00%" not in body


def test_failed_request_is_reported_without_disappearance(tmp_path):
    data(tmp_path, [row(YESTERDAY, "AAPL", "100"), row(DAY, "MSFT", "50")], failures=[{"item_id": "AAPL", "category": "http_error", "http_status": 503}], complete=False)
    _, body = issues.render_report(tmp_path)
    assert "Failed request: AAPL — http_error (HTTP 503)" in body
    assert "Confirmed disappeared: none" in body
    assert "Failed requests are not evidence of disappearance" in body


def test_recovered_stock_compares_with_its_last_valid_observation(tmp_path):
    earlier = "2026-11-14"
    data(tmp_path, [row(earlier, "AAPL", "100"), row(YESTERDAY, "MSFT", "50"), row(DAY, "AAPL", "110")])
    _, body = issues.render_report(tmp_path)
    assert "| AAPL | 100 | 110 | +10.00% |" in body


def test_new_stock_and_confirmed_disappearance_are_distinct(tmp_path):
    data(tmp_path, [row(YESTERDAY, "AAPL", "100"), row(DAY, "MSFT", "50")], new=["MSFT"], gone=["AAPL"])
    _, body = issues.render_report(tmp_path)
    assert "Newly observed by this project: MSFT" in body
    assert "Confirmed disappeared: AAPL" in body


def test_failed_request_cannot_be_reported_as_confirmed_disappearance(tmp_path):
    data(tmp_path, [row(DAY, "MSFT", "50")], gone=["AAPL"], failures=[{"item_id": "AAPL", "category": "timeout"}], complete=False)
    with pytest.raises(issues.IssueError, match="cannot confirm"):
        issues.render_report(tmp_path)


def test_same_day_issue_is_updated_and_yesterdays_is_closed_only(tmp_path):
    data(tmp_path, [row(DAY, "AAPL", "100")])
    title, body = issues.render_report(tmp_path)
    fake = FakeGitHub([project_issue(1, DAY), project_issue(2, YESTERDAY), project_issue(3, DAY, marker=False), {**project_issue(4, YESTERDAY), "pull_request": {}}])
    assert issues.publish_report(title, body, "owner/repo", "synthetic-token", fake) == "updated"
    writes = [(method, url, payload) for method, url, payload in fake.calls if method != "GET"]
    assert [method for method, _, _ in writes] == ["PATCH", "PATCH"]
    assert writes[0][1].endswith("/1") and writes[0][2]["body"] == body
    assert writes[1][1].endswith("/2") and writes[1][2] == {"state": "closed"}
    assert all(not url.endswith(("/3", "/4")) for _, url, _ in writes)


def test_new_issue_is_created_once_and_older_issue_is_not_closed(tmp_path):
    data(tmp_path, [row(DAY, "AAPL", "100")])
    title, body = issues.render_report(tmp_path)
    old = (date.fromisoformat(DAY) - timedelta(days=2)).isoformat()
    fake = FakeGitHub([project_issue(5, old), project_issue(6, DAY, marker=False)])
    assert issues.publish_report(title, body, "owner/repo", "synthetic-token", fake) == "created"
    assert [method for method, _, _ in fake.calls] == ["GET", "POST"]


def test_duplicate_project_issues_stop_all_writes(tmp_path):
    data(tmp_path, [row(DAY, "AAPL", "100")])
    title, body = issues.render_report(tmp_path)
    fake = FakeGitHub([project_issue(1, DAY), project_issue(2, DAY)])
    with pytest.raises(issues.IssueError, match="Multiple identified"):
        issues.publish_report(title, body, "owner/repo", "synthetic-token", fake)
    assert all(method == "GET" for method, _, _ in fake.calls)


def test_github_api_failure_is_sanitized(tmp_path):
    data(tmp_path, [row(DAY, "AAPL", "100")])
    title, body = issues.render_report(tmp_path)
    fake = FakeGitHub(error=403)
    with pytest.raises(issues.IssueError, match="HTTP 403") as caught:
        issues.publish_report(title, body, "owner/repo", "synthetic-token", fake)
    assert "synthetic-token" not in str(caught.value)
    assert "synthetic secret" not in str(caught.value)


def test_missing_token_stops_before_network(tmp_path, monkeypatch, capsys):
    data(tmp_path, [row(DAY, "AAPL", "100")])
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(issues, "urlopen", lambda *_args, **_kwargs: pytest.fail("network request"))
    assert issues.main(["--data-dir", str(tmp_path), "--publish", "--repo", "owner/repo"]) == 2
    assert "GITHUB_TOKEN is required" in capsys.readouterr().err


def test_offline_preview_ignores_token_and_never_uses_network(tmp_path, monkeypatch, capsys):
    data(tmp_path, [row(DAY, "AAPL", "100")])
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-secret")
    monkeypatch.setattr(issues, "urlopen", lambda *_args, **_kwargs: pytest.fail("network request"))
    assert issues.main(["--data-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert issues.TITLE_PREFIX + DAY in output
    assert "synthetic-secret" not in output


def test_complete_guard_treats_absent_data_as_incomplete(tmp_path):
    assert issues.check_complete(tmp_path, DAY) == 1


def test_complete_guard_rejects_invalid_raw_history(tmp_path):
    data(tmp_path, [row(DAY, stock.item_id, "100") for stock in STOCKS])
    with pytest.raises(issues.IssueError, match="raw snapshots"):
        issues.check_complete(tmp_path, DAY)


def test_complete_guard_accepts_matching_raw_history(tmp_path, monkeypatch):
    data(tmp_path, [row(DAY, stock.item_id, "100") for stock in STOCKS])
    content = (tmp_path / "history.csv").read_bytes()
    monkeypatch.setattr(issues, "render_history", lambda _path: (content, len(STOCKS)))
    assert issues.check_complete(tmp_path, DAY) == 0


def test_primary_backup_and_manual_gate_are_same_day_and_quota_safe(tmp_path, monkeypatch):
    now = lambda hour, minute: datetime(2026, 11, 16, hour, minute, tzinfo=timezone.utc)
    assert issues.decide_action(tmp_path, DAY, now(22, 16), schedule=issues.PRIMARY_CRON) == "skip"
    assert issues.decide_action(tmp_path, DAY, now(22, 17), schedule=issues.PRIMARY_CRON) == "collect"
    assert issues.decide_action(tmp_path, DAY, now(23, 30), schedule=issues.BACKUP_CRON) == "skip"
    assert issues.decide_action(tmp_path, DAY, now(23, 31), schedule=issues.BACKUP_CRON) == "collect"
    assert issues.decide_action(tmp_path, DAY, now(23, 31), schedule=issues.PRIMARY_CRON) == "collect"
    assert issues.decide_action(tmp_path, DAY, now(23, 45), schedule=issues.BACKUP_CRON) == "skip"
    assert issues.decide_action(tmp_path, DAY, now(0, 1), schedule=issues.BACKUP_CRON) == "skip"
    assert issues.decide_action(tmp_path, DAY, now(23, 31) + timedelta(days=1), schedule=issues.BACKUP_CRON) == "skip"
    with pytest.raises(issues.IssueError, match="quota confirmation"):
        issues.decide_action(tmp_path, DAY, now(22, 30), manual_collect=True)
    with pytest.raises(issues.IssueError, match="report-only"):
        issues.decide_action(tmp_path, DAY, now(22, 30))
    assert issues.decide_action(tmp_path, DAY, now(22, 30), manual_collect=True, quota_confirmed=True) == "collect"

    data(tmp_path, [row(DAY, stock.item_id, "100") for stock in STOCKS])
    content = (tmp_path / "history.csv").read_bytes()
    monkeypatch.setattr(issues, "render_history", lambda _path: (content, len(STOCKS)))
    assert issues.decide_action(tmp_path, DAY, now(23, 31), schedule=issues.BACKUP_CRON) == "report"
    assert issues.decide_action(tmp_path, DAY, now(23, 31)) == "report"


def test_partial_status_allows_one_backup_collection(tmp_path, monkeypatch):
    data(tmp_path, [row(DAY, "AAPL", "100")], complete=False)
    content = (tmp_path / "history.csv").read_bytes()
    monkeypatch.setattr(issues, "render_history", lambda _path: (content, 1))
    now = datetime(2026, 11, 16, 23, 31, tzinfo=timezone.utc)
    assert issues.decide_action(tmp_path, DAY, now, schedule=issues.BACKUP_CRON) == "collect"


def test_invalid_status_date_is_rejected_before_reporting(tmp_path):
    data(tmp_path, [row(DAY, "AAPL", "100")], day=YESTERDAY)
    with pytest.raises(issues.IssueError, match="observations after the status date"):
        issues.render_report(tmp_path)


def test_workflow_crons_match_tested_gate_and_have_no_extra_automatic_runs():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/daily.yml").read_text(encoding="utf-8")
    schedules = [line.split("cron:", 1)[1].strip().strip("'\"") for line in workflow.splitlines() if "- cron:" in line]
    assert schedules == [issues.PRIMARY_CRON, issues.BACKUP_CRON]
    assert "--gate --date" in workflow
    assert "cancel-in-progress: false" in workflow
