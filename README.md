# Market Data Tracker

Market Data Tracker is a graduate Financial Data Analytics project for collecting and analyzing daily stock market data. The planned scope is at least 10 publicly traded stocks, with daily open, high, low, close, and volume (OHLCV) observations. The first source adapter reads [Alpha Vantage's `TIME_SERIES_DAILY` endpoint](https://www.alphavantage.co/documentation/) in JSON format.

## Current foundation

- A minimal Python package compatible with Python 3.9 or newer
- Project metadata and pytest configuration in `pyproject.toml`
- A read-only health check that distinguishes configured foundation files from planned features
- Basic health-check tests
- An Alpha Vantage adapter and one-symbol, read-only dry-run command
- Offline raw snapshot storage and a deterministic history builder

Create a local virtual environment and install the project with its development dependencies:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Run the health check from the repository root:

```sh
.venv/bin/python -m market_data_tracker.health
```

The health check reads local project files only. It does not use an API key, access the network, or validate future features.

## Alpha Vantage dry run

Set `ALPHAVANTAGE_API_KEY` in your local environment, then run:

```sh
.venv/bin/python -m market_data_tracker.fetch --source AAPL --dry-run
```

The command makes exactly one HTTPS request for compact daily JSON, selects the latest available trading date, validates the five OHLCV values, and prints one normalized observation. It reports HTTP errors, timeouts, API errors, and rate limits. A missing key produces an error before any request. The command does not save raw responses, update history or status files, or run a production pipeline. Do not put API keys in source files, command arguments, or Git.

The normalized record uses the uppercase ticker for both `item_id` and `item`, and `Alpha Vantage` for `source`. Its measurements are:

| Field | Meaning | Unit |
| --- | --- | --- |
| `open` | First traded price in the daily bar | Listing currency per share |
| `high` | Highest traded price in the daily bar | Listing currency per share |
| `low` | Lowest traded price in the daily bar | Listing currency per share |
| `close` | Last traded price in the daily bar | Listing currency per share |
| `volume` | Shares traded during the daily bar | Shares |

`trading_date` is the date attached to the source's daily bar. `observation_date_utc` is the UTC date when this project retrieved and normalized it. They can differ: on weekends, market holidays, or before a new daily bar is available, the latest trading date may be earlier than the observation date.

Each dry run consumes one API request. [Alpha Vantage's current support page](https://www.alphavantage.co/support/api-key/) describes request limits, which may vary by account and change over time. Check the current allowance before scaling to multiple symbols. **Confirm public redistribution rights and any exchange requirements before publishing market data in production.** Review the [Alpha Vantage terms](https://www.alphavantage.co/terms_of_service/) and the applicable market-data license.

## Raw snapshots and history

Validated source-response JSON bytes can be stored separately for each stock and UTC observation date:

```text
data/
  raw/
    YYYY-MM-DD/
      alphavantage_AAPL.json.gz
      alphavantage_MSFT.json.gz
  history.csv
```

`raw_store.save_snapshot` accepts original JSON bytes, a ticker, a UTC observation date, and a data directory. It makes no network request. It validates the response before writing, then atomically replaces only that stock's file when rerun on the same UTC date. Existing snapshots from earlier dates are immutable. A validation or write failure leaves an existing good snapshot intact. The one-symbol dry-run command remains read-only; the separately gated daily workflow calls the 12-stock collector.

The full JSON response is preserved in gzip form. For history, the builder uses the optional `Meta Data` symbol for consistency checking and the `Time Series (Daily)` object. It selects the latest trading date **on or before** the snapshot folder's observation date, then reads `1. open`, `2. high`, `3. low`, `4. close`, and `5. volume` from that one bar. Other trading bars remain in raw JSON but do not become rows for that observation date. Corrupt or invalid snapshots stop the rebuild; an existing `history.csv` remains intact.

To rebuild history from raw snapshots alone, run from the repository root:

```sh
.venv/bin/python -m market_data_tracker.history
```

The command scans `data/raw`, never calls Alpha Vantage, never reads an existing history file, and writes `data/history.csv` atomically after all snapshots validate. For isolated development data, pass `--data-dir` with a temporary directory. It produces one row per ticker and UTC observation date, sorted by `date` then `item_id`, with a fixed column order and Unix line endings. Rebuilding from unchanged raw files produces identical bytes. A missing stock snapshot on a given day simply produces no row for that stock and day; it does not imply the stock disappeared.

| `history.csv` column | Definition | Unit |
| --- | --- | --- |
| `date` | UTC date of the raw observation (from its folder name) | YYYY-MM-DD |
| `item_id` | Stable uppercase stock ticker | Identifier |
| `item` | Stock ticker used as the display item | Identifier |
| `source` | Source name, `Alpha Vantage` | Name |
| `open` | Opening traded price on `trading_date` | Listing currency per share |
| `high` | Highest traded price on `trading_date` | Listing currency per share |
| `low` | Lowest traded price on `trading_date` | Listing currency per share |
| `close` | Closing traded price on `trading_date` | Listing currency per share |
| `volume` | Shares traded on `trading_date` | Shares |
| `trading_date` | Source date of the selected daily bar | YYYY-MM-DD |

On a Saturday or market holiday, `date` can be later than `trading_date`. The row records when the unchanged source bar was observed; it does **not** label Friday's closing price as a new Saturday market close. The daily Issue reporter explicitly distinguishes an unchanged source trading date from a new trading-day movement.

## Manual daily collection and status

The version-controlled watchlist contains 12 U.S. stocks:

| Ticker | Company | Ticker | Company |
| --- | --- | --- | --- |
| AAPL | Apple Inc. | MSFT | Microsoft Corp. |
| NVDA | NVIDIA Corp. | AMZN | Amazon.com Inc. |
| GOOGL | Alphabet Inc. (Class A) | META | Meta Platforms Inc. |
| TSLA | Tesla Inc. | JPM | JPMorgan Chase & Co. |
| BAC | Bank of America Corp. | WMT | Walmart Inc. |
| XOM | Exxon Mobil Corp. | JNJ | Johnson & Johnson |

Collection occurs only when this command is explicitly invoked from the repository root with `ALPHAVANTAGE_API_KEY` set in the environment:

```sh
.venv/bin/python -m market_data_tracker.collect
```

The collector requests each ticker once, waits 15 seconds between attempts, validates the response, and saves each successful original JSON response under the UTC date when that response was collected. It then rebuilds history from raw snapshots and atomically writes `data/status.json`. It makes no automatic retries. A failed ticker does not stop the others or overwrite an earlier good snapshot. A same-day rerun replaces only the successful ticker's snapshot; rebuilding history still produces one row per ticker and observation date. A later failed same-day attempt leaves an earlier valid snapshot in history.

One complete run uses 12 requests. The stated free allowance is 25 requests per day, so two complete runs use 24 requests before accounting for any other API calls. Check your account's current limit before rerunning. A complete run exits successfully; a partial or all-failed run exits with an error. `run_ok` in status is true when at least one snapshot was saved and history and status were rebuilt; `complete` is true only when all 12 tickers succeeded.

`status.json` contains these required fields:

| Field | Definition |
| --- | --- |
| `run_time` | UTC completion timestamp of the latest recorded run, in ISO 8601 format |
| `first_run_date` | Earliest UTC observation date with a valid raw snapshot, or `null` if none |
| `days_with_success` | Count of distinct UTC dates containing valid raw snapshots |
| `items` | Number of item IDs in the latest UTC observation date with valid raw snapshots |
| `new_today` | Sorted item IDs with a valid snapshot on the run's UTC date and no valid snapshot on any earlier UTC date |
| `gone_today` | Confirmed disappeared item IDs; currently empty because this per-stock API provides no reliable disappearance signal |
| `sources` | One entry for Alpha Vantage: `name`, `ok` (all 12 succeeded), and `count` (successful tickers in this run) |

Additional `failures` entries identify the ticker, sanitized failure category and message, and HTTP status when available. Partial runs can leave `items` at 12 when an earlier valid same-day snapshot survives a failed retry; `sources.count` still reports only successes from the latest run. On weekends and holidays, a new UTC observation can carry the prior trading day's bar. The collector does not calculate a new market-session movement from that unchanged bar.

On the first successful observation date, every stock successfully observed that day appears in `new_today`. On later dates, an item appears there only on its first date with a valid raw snapshot. This measures **first observation by this project**, not a new exchange listing. A stock that was already observed, then failed, then recovered is not new again. Rebuilding status from the same valid raw snapshots gives the same item movement on a same-day rerun; a failed retry does not erase an earlier successful observation. A weekend or holiday response with the same ticker and an older trading date does not create a new item. `gone_today` remains empty until a future source can establish absence independently of timeout, rate limits, malformed responses, or other per-ticker failures.

The collector has no automatic retry loop, discovery of new or delisted stocks, or authoritative disappearance signal. No production collection is performed by tests. The daily workflow remains gated until separately approved for production.

## Daily GitHub Issue report

Preview the report locally using **only** `data/history.csv` and `data/status.json`:

```sh
.venv/bin/python -m market_data_tracker.issues --data-dir /path/to/isolated/data
```

Preview is the default mode. It does not read `GITHUB_TOKEN` or make a GitHub request. `--publish` is the explicit publishing mode; it requires `GITHUB_TOKEN` and an `owner/name` from `--repo` or `GITHUB_REPOSITORY`. Do not pass a token on the command line. The publisher uses a fixed title prefix, `[market-data-tracker:daily]`, and a hidden body marker to identify its own Issues. It updates the identified same-date Issue, creates one if absent, and closes only the identified previous **calendar** day's open Issue. A duplicate identified same-date Issue stops publishing for manual review. Unrelated Issues and pull requests are never modified. API failures report the HTTP status and a safe action hint without echoing response bodies or credentials.

The movement rule is auditable: compare each ticker's latest row with its most recent earlier **valid UTC observation** for that same ticker; include a price move only when the source `trading_date` advanced. Calculate signed percentage change as `(latest close - previous close) / previous close × 100`, rank by absolute percentage change, break ties by ticker, and show the largest five. The report shows both closing prices and both source trading dates. A zero or absent previous close cannot yield a percentage change and is shown separately. An unchanged source trading date is labeled as a market-closed day or no new source bar; the report still appears even if no stock has a new trading bar. The first day states that there is no prior comparison. The report lists source successes and failures, newly observed tickers, and confirmed disappearances from status. Failed API calls never become disappearances; the current per-ticker source cannot confirm a delisting, so `gone_today` remains empty in normal collection.

## GitHub Actions deployment design

`.github/workflows/tests.yml` runs offline pytest and the health check on every push and pull request. It uses Python 3.11 and installs `.[dev]` from `pyproject.toml`; it needs no API key. Once the workflow exists on GitHub, a README badge can point to `https://github.com/OWNER/REPOSITORY/actions/workflows/tests.yml/badge.svg` after replacing the owner and repository placeholders.

`.github/workflows/daily.yml` has two daily UTC schedules, a manual recovery trigger, and one concurrency group. It runs offline tests first, checks the target UTC date, and verifies whether a complete collection already exists. A successful same-date collection causes the later run to skip Alpha Vantage and only repair/update the Issue. A partial collection can be followed by the one backup attempt. The collector has no automatic API retry loop. For new data, the workflow rebuilds `history.csv` from raw snapshots, verifies the status date and raw/history consistency, previews the report offline, and stages only `data/raw/**`, `data/history.csv`, and `data/status.json`. It checks the staged diff and Git status, commits and pushes that generated data to the configured production branch, then publishes the same-date Issue. A partial collection is reported and leaves the run failed for investigation. A GitHub-token-authored data push does not trigger another GitHub Actions push run under GitHub's normal event rules.

| Run | UTC | Central Standard Time | Central Daylight Time |
| --- | --- | --- | --- |
| Primary | 22:17 daily | 16:17 | 17:17 |
| Backup | 23:31 daily | 17:31 | 18:31 |

Both times are after the regular U.S. stock trading session. GitHub may delay or drop scheduled runs, so the workflow checks the **current UTC date** immediately before collection and publishing and skips scheduled runs outside their UTC windows. Collection starts no later than 23:44 UTC, leaving a buffer before midnight. The two runs cannot overlap because of the concurrency group. A delayed primary and backup both evaluate the stored complete status before any collection. Each complete run uses 12 Alpha Vantage requests; at most two scheduled runs can collect on one UTC date, totaling 24 against the documented 25-request daily free allowance. This assumes no other client uses that key that day. Manual collection requires the `quota_confirmed` input after checking account usage; manual report-only recovery makes no market-data requests. A manual run should be started after the market close and on the intended UTC date. No automatic retry loop is configured.

**Activation is a separate production decision.** Scheduled workflows run from the repository's default branch after the workflow is merged there. The daily job additionally requires repository variables `MARKET_DATA_AUTOMATION_ENABLED=true` and `MARKET_DATA_PRODUCTION_BRANCH` set to the approved production branch; until then it skips. Configure `ALPHAVANTAGE_API_KEY` as a GitHub Actions repository secret only at deployment. The daily workflow grants `contents: write` to commit generated data and `issues: write` to manage its marked Issues; the tests workflow grants only `contents: read`. Repository and branch protection settings must permit the selected production branch update by `GITHUB_TOKEN`. The project never stores an API key or token in the repository. **Public redistribution permission for Alpha Vantage and exchange market data remains a production-launch checkpoint.**

To inspect a failed scheduled run, open its Actions log, identify the first failing step, and check whether it was skipped by the UTC window, lacked the secret or permissions, had invalid raw/history/status data, hit a source error or rate limit, or failed to push or publish. The Issue reporter omits credential-bearing source messages. Inspect `data/status.json` and the dated raw snapshots on the approved branch. If a complete valid collection exists, use manual `collect=false` to repair the Issue without spending API requests. If incomplete, check the account's remaining daily quota before a manual `collect=true` recovery, or wait for the next scheduled UTC date. Never repeat a 12-request collection blindly.

The seven-day requirement is measured by `days_with_success` in `data/status.json`: seven **distinct UTC observation dates with valid snapshots**, not seven trading dates and not seven runs on one day. The Issue shows progress as `n/7`. Before declaring the requirement met, inspect the raw dated directories, rebuild history, verify status, and ensure at least seven distinct dates have genuine successful source responses. Scheduled workflows and production data have not been activated or generated in this repository yet.

Run the tests with pytest:

```sh
.venv/bin/python -m pytest -v
```

The original health-check tests can also run without pytest using `python3 -m unittest discover -s tests -v`. The adapter tests require pytest.

## Website: Today, Browse, and Trends

The static website uses `index.html` (Today), `browse.html`, and `trends.html`, with shared vanilla JavaScript and locally stored CSS. Production pages fetch `data/history.csv` and `data/status.json` using relative paths, so they can run under a GitHub Pages project path without a build step. The website does not call Alpha Vantage or any other market API. Until real collection data is approved and published, normal mode shows a clear unavailable-data message instead of fabricated metrics.

To preview the site from the repository root, run:

```sh
python3 -m http.server 8000
```

Then open **http://localhost:8000/index.html?demo=1**. The `?demo=1` query loads only `tests/fixtures/site_demo/history.csv` and `tests/fixtures/site_demo/status.json`. A persistent **DEMO DATA — NOT LIVE** banner appears on every page, and the Today/Browse/Trends links preserve demonstration mode. The fixture values and one simulated failed stock request are synthetic. Do not copy these files into `data/`. Browser `fetch()` needs an HTTP server; opening the HTML through `file://` is not a supported preview method. Stop the server with Ctrl+C.

Today distinguishes the UTC observation date from the source trading date, shows operational counts from `status.json`, and uses history rows for OHLCV values and price comparisons. The watchlist includes older available values when the latest request failed, visibly marked as such. The dashboard separates configured stocks, valid latest-date snapshots, latest-run request successes, and complete versus incomplete source runs. A repeated weekend or holiday bar is labeled **No new bar** and cannot appear as a new price move. Browse supports company/ticker search, independent closing-price and observation-status filters, ascending/descending sort on all five numeric fields, and a browser-local favorites shortlist. Sector/category is shown as **Not supplied** because the backend CSV has no reliable sector field. Favorites fall back to session memory when browser storage is unavailable.

Trends renders opening, high, low, closing, and volume charts for selected stocks using native SVG. Browser calculations deduplicate repeated observations of the same source `trading_date` per ticker. The four findings are: first-to-latest closing performance, sample standard deviation of consecutive closing returns, largest absolute consecutive-session volume change, and average `(high − low) / open × 100` intraday range. Each section displays its specific inputs, method, result, and chart; unavailable denominators or insufficient sessions produce explanations instead of invented numbers. Prices use USD per share and volume uses shares.

The website's test checks validate the actual backend CSV/status contract, synthetic weekend behavior, page links and controls, relative fetch paths, and pure JavaScript calculations when a JavaScript runtime is available. Browser layout, keyboard interaction, and local storage should also be checked in a real browser before production launch. Public market-data redistribution permission remains a separate production checkpoint. A generated-data push made with `GITHUB_TOKEN` does not itself trigger a GitHub Pages build; production site deployment needs its own approved publishing path.

## Planned features and phases

1. **Foundation (current):** package structure, documentation, health check, and tests.
2. **Source adapter (current):** read and validate one symbol through an isolated dry run.
3. **Manual data collection (current):** collect the 12-stock watchlist once per explicit invocation and report status.
4. **Data management (current):** store raw snapshots and rebuild reproducible history.
5. **Analysis (initial website complete):** calculate browser-side findings and visualizations from history CSV.
6. **Presentation (initial website complete):** three responsive static pages with a clearly labeled synthetic demonstration mode.

The daily Issue reporter, gated workflows, and initial website are implemented locally. Production activation and real-data website publication remain future work. No production market data or API credentials are included in the repository.
