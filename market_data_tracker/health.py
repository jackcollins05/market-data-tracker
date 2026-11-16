"""Read-only status report for the project foundation and planned features."""

from pathlib import Path
from typing import List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent

FOUNDATION_FILES = (
    ("Project documentation", "README.md"),
    ("Git ignore rules", ".gitignore"),
    ("Package configuration", "pyproject.toml"),
    ("Python package", "market_data_tracker/__init__.py"),
    ("Health check", "market_data_tracker/health.py"),
    ("Health-check tests", "tests/test_health.py"),
    ("Alpha Vantage source adapter", "market_data_tracker/alpha_vantage.py"),
    ("Read-only dry run", "market_data_tracker/fetch.py"),
    ("Raw snapshot storage", "market_data_tracker/raw_store.py"),
    ("Reproducible history builder", "market_data_tracker/history.py"),
    ("Stock watchlist", "market_data_tracker/config.py"),
    ("Manual daily collector", "market_data_tracker/collect.py"),
    ("Collection status generator", "market_data_tracker/status.py"),
)

PLANNED_COMPONENTS = (
    "Scheduled collection and monitoring",
    "Seven distinct successful collection dates",
    "Financial analytics and visualizations",
    "Website or dashboard",
)


def foundation_statuses(root: Path = PROJECT_ROOT) -> List[Tuple[str, bool]]:
    """Check whether each foundation file exists without changing project state."""
    return [(name, (root / relative_path).is_file()) for name, relative_path in FOUNDATION_FILES]


def main() -> int:
    """Print current status; fail only if a foundation file is missing."""
    statuses = foundation_statuses()

    print("Market Data Tracker health check")
    print("Configured foundation components:")
    for name, configured in statuses:
        state = "configured" if configured else "missing"
        print("  {}: {}".format(name, state))

    print("Planned components:")
    for name in PLANNED_COMPONENTS:
        print("  {}: not yet implemented".format(name))

    return 0 if all(configured for _, configured in statuses) else 1


if __name__ == "__main__":
    raise SystemExit(main())
