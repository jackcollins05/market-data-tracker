# Market Data Tracker

Market Data Tracker is a graduate Financial Data Analytics project for collecting and analyzing daily stock market data. The planned scope is at least 10 publicly traded stocks, with daily open, high, low, close, and volume (OHLCV) observations. Alpha Vantage has been identified as a potential data source, but the project does not yet connect to it or retrieve data.

## Current foundation

- A minimal Python package compatible with Python 3.9 or newer
- Project metadata and pytest configuration in `pyproject.toml`
- A read-only health check that distinguishes configured foundation files from planned features
- Basic health-check tests

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

Run the tests with pytest:

```sh
.venv/bin/python -m pytest -v
```

The tests can also run without pytest using `python3 -m unittest discover -s tests -v`.

## Planned features and phases

1. **Foundation (current):** package structure, documentation, health check, and tests.
2. **Data collection:** configure credentials through environment variables, retrieve daily OHLCV data for at least 10 stocks, and handle API limits and errors.
3. **Data management:** validate observations, store them reproducibly, and support incremental updates.
4. **Analysis:** calculate and present useful financial metrics and visualizations.
5. **Presentation:** build a website or dashboard after the data and analysis layers are established.

The data pipeline, storage, analytics, and website are not implemented in this phase. No market data or API credentials are included in the repository.
