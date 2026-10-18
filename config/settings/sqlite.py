"""Settings for the test suite: an in-memory database and a throwaway
document folder.

pytest.ini selects these. Uploads made by the API tests land in a temporary
folder, not in data/input, which otherwise collected a copy of every test PDF
on every run.
"""
import tempfile
from pathlib import Path

from .base import *  # noqa: F401,F403

DOCUMENT_ROOT = Path(tempfile.mkdtemp(prefix="ocr-balance-test-"))

DEBUG = True
ALLOWED_HOSTS = ["*"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {"NAME": ":memory:"},
    }
}
