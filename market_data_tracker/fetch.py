"""Read-only command for one Alpha Vantage daily stock observation."""

import argparse

from market_data_tracker.alpha_vantage import AlphaVantageError, fetch_latest_daily


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inspect one Alpha Vantage daily observation.")
    parser.add_argument("--source", required=True, help="Stock ticker, for example AAPL")
    parser.add_argument("--dry-run", action="store_true", help="Fetch once and print without writing data")
    args = parser.parse_args(argv)
    if not args.dry_run:
        parser.error("Only --dry-run is supported; the production pipeline is not implemented.")

    try:
        record = fetch_latest_daily(args.source)
    except AlphaVantageError as error:
        status = " (HTTP {})".format(error.http_status) if error.http_status is not None else ""
        print("Failure [{}]{}: {}".format(error.category, status, error))
        return 1
    except Exception:
        # Unexpected transport errors may include a URL with the key in their text.
        print("Failure [internal_error]: Unexpected dry-run error.")
        return 1

    print("Success: {} (HTTP {})".format(record.item_id, record.http_status))
    print("Trading date: {}".format(record.trading_date))
    print("Observation date (UTC): {}".format(record.observation_date_utc))
    print("Open: {}".format(record.open))
    print("High: {}".format(record.high))
    print("Low: {}".format(record.low))
    print("Close: {}".format(record.close))
    print("Volume: {}".format(record.volume))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
