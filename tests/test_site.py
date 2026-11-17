"""Static site contract checks and pure JavaScript calculations, with no network."""

import csv
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from market_data_tracker.history import COLUMNS
from market_data_tracker.status import REQUIRED_KEYS


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "tests" / "fixtures" / "site_demo"


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.nav = set()
        self.scripts = set()
        self.page = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag == "a" and "data-nav" in attrs:
            self.nav.add((attrs["data-nav"], attrs.get("href")))
        if tag == "script":
            self.scripts.add(attrs.get("src"))
        if tag == "body":
            self.page = attrs.get("data-page")


@pytest.mark.parametrize("filename,page,content_id", [
    ("index.html", "today", "today-content"),
    ("browse.html", "browse", "browse-content"),
    ("trends.html", "trends", "trends-content"),
])
def test_site_pages_have_shared_navigation_and_semantics(filename, page, content_id):
    html = (ROOT / filename).read_text(encoding="utf-8")
    parsed = PageParser()
    parsed.feed(html)
    assert parsed.page == page
    assert parsed.nav == {("today", "index.html"), ("browse", "browse.html"), ("trends", "trends.html")}
    assert {"main", "demo-banner", "data-message", content_id}.issubset(parsed.ids)
    assert "assets/js/app.js" in parsed.scripts
    assert 'href="#main"' in html
    assert "DEMO DATA — NOT LIVE" in html


def test_demo_fixture_uses_backend_columns_and_status_contract():
    with (DEMO / "history.csv").open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        assert tuple(reader.fieldnames) == COLUMNS
        rows = list(reader)
    status = json.loads((DEMO / "status.json").read_text(encoding="utf-8"))
    assert set(REQUIRED_KEYS).issubset(status)
    assert status["demo"] is True
    assert status["observation_date_utc"] == "2026-11-16"
    assert status["days_with_success"] == len({row["date"] for row in rows})
    assert status["items"] == len({row["item_id"] for row in rows if row["date"] == status["observation_date_utc"]})
    assert len({row["item_id"] for row in rows}) == 12
    assert {item["item_id"] for item in status["failures"]} == {"XOM"}
    assert status["gone_today"] == []
    friday = {row["item_id"]: row for row in rows if row["date"] == "2026-11-13"}
    saturday = {row["item_id"]: row for row in rows if row["date"] == "2026-11-14"}
    assert friday.keys() == saturday.keys()
    assert all(friday[ticker]["trading_date"] == saturday[ticker]["trading_date"] and friday[ticker]["close"] == saturday[ticker]["close"] for ticker in friday)
    assert all(float(row["high"]) >= max(float(row["open"]), float(row["close"])) and float(row["low"]) <= min(float(row["open"]), float(row["close"])) for row in rows)


def test_site_loads_only_relative_production_or_demo_data_paths():
    source = (ROOT / "assets" / "js" / "data.js").read_text(encoding="utf-8")
    assert "data/" in source
    assert "tests/fixtures/site_demo/" in source
    assert "fetcher(`${root}history.csv`" in source
    assert "fetcher(`${root}status.json`" in source
    assert "?demo=1" in source
    assert "http://" not in source and "https://" not in source


def test_browse_has_two_independent_filters_all_numeric_sorts_and_favorites():
    html = (ROOT / "browse.html").read_text(encoding="utf-8")
    for field in ("open", "high", "low", "close", "volume"):
        assert f'data-sort="{field}"' in html
    assert 'id="price-filter"' in html and 'id="state-filter"' in html
    assert 'id="stock-search"' in html and 'id="favorites-only"' in html
    assert "localStorage" in (ROOT / "assets" / "js" / "app.js").read_text(encoding="utf-8")


def test_trends_has_five_metric_charts_and_four_calculation_methods():
    core = (ROOT / "assets" / "js" / "core.js").read_text(encoding="utf-8")
    app = (ROOT / "assets" / "js" / "app.js").read_text(encoding="utf-8")
    assert "['open', 'high', 'low', 'close']" in core
    assert "'volume'" in core
    for method in ("performance", "volatility", "volume", "range"):
        assert f"result.{method}" in app
    assert "tradingSessions(rows, ticker)" in app
    assert "barChart(plot, entries" in app and "lineChart(chartRefs.get(field)" in app


def test_responsive_accessible_style_and_safe_dom_rendering():
    css = (ROOT / "assets" / "css" / "site.css").read_text(encoding="utf-8")
    assert "@media (max-width: 760px)" in css
    assert "@media (max-width: 420px)" in css
    assert ":focus-visible" in css
    assert ".table-scroll" in css
    for filename in ("app.js", "charts.js"):
        assert "innerHTML" not in (ROOT / "assets" / "js" / filename).read_text(encoding="utf-8")


JS_ASSERTIONS = r"""
function check(condition, message) { if (!condition) throw new Error(message); }
const header = COLUMNS.join(',');
const quoted = parseCSV(header + '\n2026-11-16,AAPL,"Apple, ""Inc."" Co",Alpha Vantage,1,2,0,1,10,2026-11-16\n');
check(quoted[0].item === 'Apple, "Inc." Co', 'quoted CSV parser');
check(numberOrNull('') === null && numberOrNull('12.5') === 12.5, 'numeric conversion');
check(numberOrNull('NaN') === null && numberOrNull('Infinity') === null, 'invalid numeric values');
check(validDate('2026-11-16') && !validDate('2026-02-30'), 'UTC date validation');
check(formatDate('2026-11-16') !== 'Unavailable' && formatMoney(10.5).indexOf('$') >= 0, 'date and currency formatting');
check(formatPercent(-12.5, true) === '-12.50%', 'percentage formatting');
const make = (ticker, day, trade, close, volume) => ({ item_id: ticker, date: day, trading_date: trade, source: 'Alpha Vantage', open: close - 1, high: close + 2, low: close - 3, close, volume });
const rows = [
  make('AAPL', '2026-11-10', '2026-11-10', 100, 1000),
  make('AAPL', '2026-11-11', '2026-11-11', 105, 1500),
  make('AAPL', '2026-11-12', '2026-11-12', 120, 1200),
  make('AAPL', '2026-11-14', '2026-11-12', 120, 1200),
  make('MSFT', '2026-11-10', '2026-11-10', 200, 2000),
  make('MSFT', '2026-11-11', '2026-11-11', 190, 1800),
  make('MSFT', '2026-11-12', '2026-11-12', 180, 1700),
];
check(tradingSessions(rows, 'AAPL').length === 3, 'weekend trading session deduplication');
check(compareClose(rows[3], rows[2]).kind === 'unchanged-bar', 'weekend is not a new move');
check(compareClose(rows[2], rows[1]).percent > 0, 'positive close movement');
check(compareClose(rows[4], null).kind === 'first', 'first observation');
check(compareClose(make('AAPL','2026-11-13','2026-11-13',120,100), make('AAPL','2026-11-12','2026-11-12',0,100)).kind === 'unavailable', 'zero denominator');
const findings = calculateFindings(rows);
check(findings.performance[0].ticker === 'AAPL' && Math.abs(findings.performance[0].value - 20) < 1e-9, 'closing performance');
check(findings.volatility.length === 2 && findings.volatility.every((item) => Number.isFinite(item.value)), 'volatility');
check(Math.abs(findings.volatility.find((item) => item.ticker === 'AAPL').value - 6.566) < 0.01, 'sample volatility value');
check(findings.volume[0].ticker === 'AAPL' && Math.abs(findings.volume[0].value - 50) < 1e-9, 'volume change');
check(findings.range.length === 2 && findings.range.every((item) => Number.isFinite(item.value)), 'intraday range');
check(Math.abs(findings.range.find((item) => item.ticker === 'AAPL').value - 4.6866) < 0.01, 'average range value');
const insufficient = calculateFindings([make('JNJ', '2026-11-10', '2026-11-10', 100, 10)]);
check(insufficient.performance.length === 0 && insufficient.volatility.length === 0 && insufficient.volume.length === 0, 'insufficient history');
const latest = latestByTicker(rows);
const status = { observation_date_utc: '2026-11-12', failures: [{ item_id: 'MSFT' }] };
check(filterBrowse(['AAPL','MSFT'], latest, status, { query: 'Apple' }).join(',') === 'AAPL', 'company search');
check(filterBrowse(['AAPL','MSFT'], latest, status, { query: 'MSFT' }).join(',') === 'MSFT', 'ticker search');
check(filterBrowse(['AAPL','MSFT'], latest, status, { observation: 'Failed latest request' }).join(',') === 'MSFT', 'observation filter');
check(filterBrowse(['AAPL','MSFT'], latest, status, { priceBand: '200-plus' }).length === 0, 'price filter');
check(filterBrowse(['AAPL','MSFT'], latest, status, { priceBand: '100-199', observation: 'Failed latest request' }).join(',') === 'MSFT', 'independent combined filters');
check(filterBrowse(['AAPL','MSFT'], latest, status, { favoritesOnly: true, favorites: new Set(['MSFT']) }).join(',') === 'MSFT', 'favorites filter');
check(filterBrowse(['AAPL','MSFT'], latest, status, { query: 'not-a-company' }).length === 0, 'empty search result');
const storage = { value: null, getItem() { return this.value; }, setItem(_key, value) { this.value = value; } };
check(writeFavorites(storage, new Set(['AAPL'])) && readFavorites(storage).has('AAPL'), 'favorites persist after reload');
check(!writeFavorites(null, new Set(['MSFT'])) && readFavorites(null).size === 0, 'unavailable storage fallback');
const withMissing = new Map(latest);
withMissing.set('MSFT', { ...latest.get('MSFT'), close: null });
check(filterBrowse(['AAPL','MSFT'], withMissing, status, { priceBand: 'unavailable' }).join(',') === 'MSFT', 'missing close filter');
check(filterBrowse(['AAPL','MSFT'], withMissing, status, { sort: 'close', direction: 'asc' })[1] === 'MSFT', 'missing numeric sorts last');
for (const field of NUMERIC_FIELDS) {
  const asc = filterBrowse(['AAPL','MSFT'], latest, status, { sort: field, direction: 'asc' });
  const desc = filterBrowse(['AAPL','MSFT'], latest, status, { sort: field, direction: 'desc' });
  check(asc[0] !== desc[0], 'both sort directions for ' + field);
}
'site core calculations passed';
"""


def test_pure_javascript_calculations_with_available_runtime(tmp_path):
    source = (ROOT / "assets" / "js" / "core.js").read_text(encoding="utf-8").replace("export ", "")
    data_source = (ROOT / "assets" / "js" / "data.js").read_text(encoding="utf-8")
    data_source = re.sub(r"^import[\s\S]*?;\n", "", data_source, flags=re.MULTILINE).replace("export ", "")
    demo_csv = (DEMO / "history.csv").read_text(encoding="utf-8")
    demo_status = (DEMO / "status.json").read_text(encoding="utf-8")
    fixture_assertions = (
        "\ncheck(pageHref('browse', true) === 'browse.html?demo=1', 'demo navigation');\n"
        "check(pageHref('trends', false) === 'trends.html', 'production navigation');\n"
        "check(responseError({status: 404}, 'History CSV', false) === 'Production data has not been collected yet.', '404 missing production');\n"
        "check(responseError({status: 503}, 'History CSV', false).includes('HTTP 503'), 'other HTTP failure');\n"
        "function expectError(fn, expected) { try { fn(); } catch (error) { check(error.message.includes(expected), expected + ' classification'); return; } throw new Error('Expected ' + expected); }\n"
        "expectError(() => parseDatasetTexts('<!doctype html><html></html>', '{}'), 'Production data has not been collected yet.');\n"
        "expectError(() => parseDatasetTexts('bad,header\\n', '{}'), 'schema does not match');\n"
        "expectError(() => parseDatasetTexts(COLUMNS.join(',') + '\\nAAPL,short\\n', '{}'), 'structure is invalid');\n"
        "expectError(() => parseDatasetTexts(COLUMNS.join(',') + '\\n', '{}'), 'no observation rows');\n"
        "expectError(() => parseDatasetTexts(COLUMNS.join(',') + '\\n2026-11-16,AAPL,AAPL,Alpha Vantage,1,2,0,1,10,2026-11-16\\n', '{broken'), 'Status JSON is invalid');\n"
        f"const demoRows = normalizeHistory({json.dumps(demo_csv)});\n"
        f"const demoStatus = validateStatus({demo_status});\n"
        f"check(parseDatasetTexts({json.dumps(demo_csv)}, {json.dumps(demo_status)}, true).rows.length === 66, 'demo fixture path parsing');\n"
        "check(demoRows.length === 66 && demoStatus.demo === true, 'real synthetic fixture parsing');\n"
        "check(tradingSessions(demoRows, 'AAPL').length === 5, 'demo weekend deduplication');\n"
        "const demoFindings = calculateFindings(demoRows);\n"
        "check(Object.values(demoFindings).every((entries) => entries.length > 0), 'all four fixture findings');\n"
        "'site fixture calculations passed';\n"
    )
    script = tmp_path / "site_core_check.js"
    script.write_text(source + "\n" + data_source + "\n" + JS_ASSERTIONS + fixture_assertions, encoding="utf-8")
    if shutil.which("node"):
        command = ["node", str(script)]
    elif shutil.which("osascript"):
        command = ["osascript", "-l", "JavaScript", str(script)]
    else:
        pytest.skip("No JavaScript runtime is available for pure calculation checks.")
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=20)
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize("module", ["core.js", "data.js", "charts.js", "app.js"])
def test_browser_javascript_modules_parse(module, tmp_path):
    source = (ROOT / "assets" / "js" / module).read_text(encoding="utf-8")
    if shutil.which("node"):
        target = tmp_path / (Path(module).stem + ".mjs")
        target.write_text(source, encoding="utf-8")
        command = ["node", "--check", str(target)]
    elif shutil.which("osascript"):
        stripped = re.sub(r"^import[\s\S]*?;\n", "", source, flags=re.MULTILINE).replace("export ", "")
        target = tmp_path / (Path(module).stem + "-syntax.js")
        target.write_text("new Function(" + json.dumps(stripped) + ");\n'parsed';\n", encoding="utf-8")
        command = ["osascript", "-l", "JavaScript", str(target)]
    else:
        pytest.skip("No JavaScript runtime is available for syntax checks.")
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=20)
    assert result.returncode == 0, result.stderr or result.stdout
