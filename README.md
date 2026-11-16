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

`raw_store.save_snapshot` accepts original JSON bytes, a ticker, a UTC observation date, and a data directory. It makes no network request. It validates the response before writing, then atomically replaces only that stock's file when rerun on the same UTC date. Existing snapshots from earlier dates are immutable. A validation or write failure leaves an existing good snapshot intact. The current dry-run command remains read-only; no production collection workflow calls the storage function yet.

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

On a Saturday or market holiday, `date` can be later than `trading_date`. The row records when the unchanged source bar was observed; it does **not** label Friday's closing price as a new Saturday market close. Market-closure indicators and same-day comparisons are planned for a later phase.

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

This milestone has no scheduler, automatic retries, discovery of new or delisted stocks, or authoritative disappearance signal. The seven required distinct successful collection dates must be achieved through future explicit runs or scheduling work; no production collection is performed by tests.

Run the tests with pytest:

```sh
.venv/bin/python -m pytest -v
```

The original health-check tests can also run without pytest using `python3 -m unittest discover -s tests -v`. The adapter tests require pytest.

## Planned features and phases

1. **Foundation (current):** package structure, documentation, health check, and tests.
2. **Source adapter (current):** read and validate one symbol through an isolated dry run.
3. **Manual data collection (current):** collect the 12-stock watchlist once per explicit invocation and report status.
4. **Data management (current):** store raw snapshots and rebuild reproducible history.
5. **Analysis:** calculate and present useful financial metrics and visualizations.
6. **Presentation:** build a website or dashboard after the data and analysis layers are established.

Automated scheduling, monitoring, analytics, and the website are not implemented in this phase. No market data or API credentials are included in the repository.
