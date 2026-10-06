"""Run the service against a file-backed SQLite database.

For running the service on a machine without PostgreSQL. Nothing here needs a
particular database: the service stores the rows it read and hands them back,
with no locking and no concurrent writers.

PostgreSQL stays the default in ``development`` and ``production`` because that
is what this will be deployed against; this module is for local work and demos.

    .\\.venv\\Scripts\\python.exe manage.py migrate --settings=config.settings.local_sqlite
    .\\.venv\\Scripts\\python.exe manage.py runserver 0.0.0.0:3004 --settings=config.settings.local_sqlite

``config.settings.sqlite`` is the in-memory variant the test suite uses; this one
keeps the data between runs.
"""
from pathlib import Path

from .base import *  # noqa: F401,F403
from .base import BASE_DIR

DEBUG = True
ALLOWED_HOSTS = ["*"]

_DB_DIR = Path(BASE_DIR) / "data"
_DB_DIR.mkdir(parents=True, exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(_DB_DIR / "local.sqlite3"),
    }
}
