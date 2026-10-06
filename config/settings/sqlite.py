"""Settings for running the suite without a SQL Server instance.

Used by ``scripts\\test.ps1 -Sqlite`` and by CI stages that only need the
pure-logic tests. The queue-claim tests skip themselves here, because claiming
uses SQL Server table hints (see apps/transactions/queue.py); run the full
suite against SQL Server before releasing.
"""
from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {"NAME": ":memory:"},
    }
}
