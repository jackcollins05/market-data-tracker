# Market Data Tracker

Market Data Tracker is a graduate Financial Data Analytics project for collecting and analyzing daily stock market data. The planned scope is at least 10 publicly traded stocks, with daily open, high, low, close, and volume (OHLCV) observations. The first source adapter reads [Alpha Vantage's `TIME_SERIES_DAILY` endpoint](https://www.alphavantage.co/documentation/) in JSON format.

## Current foundation

- A minimal Python package compatible with Python 3.9 or newer
- Project metadata and pytest configuration in `pyproject.toml`
- A read-only health check that distinguishes configured foundation files from planned features
- Basic health-check tests
- An Alpha Vantage adapter and one-symbol, read-only dry-run command

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

Run the tests with pytest:

```sh
.venv/bin/python -m pytest -v
```

The original health-check tests can also run without pytest using `python3 -m unittest discover -s tests -v`. The adapter tests require pytest.

## Planned features and phases

1. **Foundation (current):** package structure, documentation, health check, and tests.
2. **Source adapter (current):** read and validate one symbol through an isolated dry run.
3. **Data collection:** schedule or run updates for at least 10 stocks within API limits.
4. **Data management:** store observations reproducibly and support incremental updates.
5. **Analysis:** calculate and present useful financial metrics and visualizations.
6. **Presentation:** build a website or dashboard after the data and analysis layers are established.

The production data pipeline, storage, analytics, and website are not implemented in this phase. No market data or API credentials are included in the repository.
