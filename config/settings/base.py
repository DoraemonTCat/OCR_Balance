"""Shared Django settings.

The database is PostgreSQL. The service owns only its own ``balance_*`` and
``django_*`` tables, so DB_* can point at a server shared with anything else;
nothing here assumes a particular host or database.

``config.settings.local_sqlite`` runs the same code against a local file
instead, for a machine with no PostgreSQL.

Timestamps are timezone-aware (TIMESTAMPTZ, USE_TZ = True); TIME_ZONE only
controls how they are rendered.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]

load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = env(name, "").strip()
    return int(raw) if raw else default


def env_float(name: str, default: float) -> float:
    raw = env(name, "").strip()
    return float(raw) if raw else default


def env_list(name: str, default: str = "") -> list[str]:
    raw = env(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# --- TLS trust -------------------------------------------------------------
# On a machine whose antivirus or proxy inspects TLS, Python has to be told to
# trust that product's root as well, or PaddleOCR cannot download its models.
# scripts/setup_certs.py builds the bundle; REQUESTS_CA_BUNDLE points at it.
# The path is resolved against the project root so it works whatever the working
# directory is, and is mirrored to SSL_CERT_FILE so plain ``ssl`` uses it too.
_ca_bundle = env("REQUESTS_CA_BUNDLE").strip()
if _ca_bundle:
    _resolved = Path(_ca_bundle)
    if not _resolved.is_absolute():
        _resolved = BASE_DIR / _resolved
    if _resolved.is_file():
        os.environ["REQUESTS_CA_BUNDLE"] = str(_resolved)
        os.environ.setdefault("SSL_CERT_FILE", str(_resolved))


APP_NAME = env("APP_NAME", "ocr-balance")
APP_ENV = env("APP_ENV", "development")

SECRET_KEY = env("DJANGO_SECRET_KEY", "insecure-development-key")
DEBUG = False
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.core",
    "apps.balance",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "apps.core.middleware.CorsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

# --- Database: PostgreSQL --------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", "ocr_balance"),
        "USER": env("DB_USER", "postgres"),
        "PASSWORD": env("DB_PASSWORD", ""),
        "HOST": env("DB_HOST", "localhost"),
        "PORT": env("DB_PORT", "5432"),
        # The worker holds one connection for the length of a document, so
        # persistent connections buy nothing and would keep idle transactions
        # open; the API is fronted by Gunicorn workers that reconnect cheaply.
        "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 0),
        "OPTIONS": {
            # verify-full in production; the local container has no CA.
            "sslmode": env("DB_SSLMODE", "prefer"),
            "connect_timeout": env_int("DB_CONNECT_TIMEOUT", 10),
            # Abort a runaway query instead of pinning a worker on it.
            "options": f"-c statement_timeout={env_int('DB_STATEMENT_TIMEOUT_MS', 60000)}",
        },
        "TEST": {"NAME": env("TEST_DB_NAME", "ocr_balance_test")},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- I18N / TZ -------------------------------------------------------------

LANGUAGE_CODE = "th"
TIME_ZONE = env("APP_TIMEZONE", "Asia/Bangkok")
USE_I18N = True
# PostgreSQL TIMESTAMPTZ: instants are stored in UTC and rendered in TIME_ZONE.
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

REST_FRAMEWORK = {
    # Compact JSON by default, indented when the request says ?pretty.
    "DEFAULT_RENDERER_CLASSES": ["apps.core.renderers.PrettyJSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    # DRF otherwise reserves ?format= for renderer selection and answers 404
    # for any value that is not a renderer name. Only one renderer is
    # configured, so nothing is lost by turning the override off.
    "URL_FORMAT_OVERRIDE": None,
}

CORS_ALLOW_ORIGINS = env_list(
    "CORS_ALLOW_ORIGINS", "http://localhost:3002,http://127.0.0.1:3002"
)

# --- Storage / OCR ---------------------------------------------------------

#: Submitted documents live under here; uploads land in ``input/``.
DOCUMENT_ROOT = Path(env("DOCUMENT_ROOT", str(BASE_DIR / "data"))).resolve()

OCR = {
    # PaddleOCR 2.9.1 ships no Thai recognition model - its languages are ch,
    # en, korean, japan, chinese_cht, ta, te, ka, latin, arabic, cyrillic and
    # devanagari - and asserts on anything else. "latin" is the usable default:
    # it reads the drug names, batch numbers, dates and quantities on the
    # psychotropic ledgers, which are written in Latin script and digits. The
    # Thai cells on those forms come back as noise and are flagged for review
    # rather than reported as read. See docs/OCR_THAI.md.
    "LANGUAGE": env("OCR_LANGUAGE", "latin"),
    "USE_GPU": env_bool("OCR_USE_GPU", False),
    # Pages are rendered at this dpi before the table rules are detected.
    # 300 rather than 200: at 200 the recogniser misses the quantity cells on
    # the fainter pages altogether, and a page with no quantities yields no
    # rows at all - on the sample it cost 4 of the 45 entries, 3 of them from
    # one page. The cost is roughly half again as long per page.
    "RENDER_DPI": env_int("OCR_RENDER_DPI", 300),
}

BULK_INSERT_BATCH_SIZE = env_int("BULK_INSERT_BATCH_SIZE", 1000)

LOG_LEVEL = env("LOG_LEVEL", "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "config.logging.JsonFormatter"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json"},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "propagate": True},
    },
}
