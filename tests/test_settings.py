"""Guards on the database configuration."""
import importlib

from django.test import SimpleTestCase

base = importlib.import_module("config.settings.base")


class DatabaseConfigTests(SimpleTestCase):
    def test_engine_is_postgresql(self):
        self.assertEqual(
            base.DATABASES["default"]["ENGINE"], "django.db.backends.postgresql"
        )

    def test_sslmode_is_configurable(self):
        self.assertIn("sslmode", base.DATABASES["default"]["OPTIONS"])

    def test_a_statement_timeout_is_set(self):
        """A runaway query must not pin a worker or an API process forever."""
        options = base.DATABASES["default"]["OPTIONS"]["options"]
        self.assertIn("statement_timeout", options)

    def test_test_database_name_is_separate(self):
        """Tests must never run against the working database.

        Compared through the environment, not through DATABASES: while the test
        runner is active it has already swapped NAME to the test database.
        """
        configured = base.env("DB_NAME", "ocr_balance")
        test_name = base.env("TEST_DB_NAME", "ocr_balance_test")
        self.assertTrue(test_name)
        self.assertNotEqual(test_name, configured)


class TimezoneTests(SimpleTestCase):
    def test_timestamps_are_timezone_aware(self):
        """PostgreSQL stores TIMESTAMPTZ; naive datetimes would be ambiguous."""
        self.assertTrue(base.USE_TZ)

    def test_display_timezone_is_bangkok(self):
        self.assertEqual(base.TIME_ZONE, "Asia/Bangkok")
