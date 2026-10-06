"""Production settings. All secrets come from the environment."""
from .base import *  # noqa: F401,F403
from .base import env, env_list

DEBUG = False
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

if not env("DJANGO_SECRET_KEY"):
    raise RuntimeError("DJANGO_SECRET_KEY must be set in production")
if not env("DB_PASSWORD"):
    raise RuntimeError("DB_PASSWORD must be set in production")
