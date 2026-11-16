"""Tests for the read-only health check (also collected by pytest)."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from market_data_tracker import health


class HealthCheckTests(unittest.TestCase):
    def test_foundation_files_are_configured(self):
        statuses = health.foundation_statuses()
        self.assertEqual(len(statuses), len(health.FOUNDATION_FILES))
        self.assertTrue(all(configured for _, configured in statuses))

    def test_report_marks_planned_features_as_unimplemented(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = health.main()

        self.assertEqual(exit_code, 0)
        for name in health.PLANNED_COMPONENTS:
            self.assertIn("{}: not yet implemented".format(name), output.getvalue())

    def test_missing_foundation_file_causes_failure(self):
        with patch.object(
            health,
            "foundation_statuses",
            return_value=[("Project documentation", False)],
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = health.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("Project documentation: missing", output.getvalue())


if __name__ == "__main__":
    unittest.main()
