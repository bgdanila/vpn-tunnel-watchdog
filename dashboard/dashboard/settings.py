"""Django settings for the VPN watchdog dashboard.

Two ways this can run:

* Local — reads the daemon's `/var/log/vpn_watchdog*.log` files (or the
  fallback location under `~/.vpn_watchdog/` / `local_logs/`).
* Demo / Vercel — set `VPN_WATCHDOG_DEMO=1` to render the bundled
  sample data without needing root or WireGuard at all.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent          # dashboard/
PROJECT_ROOT = BASE_DIR.parent                              # repo root

# Make the top-level `daemon` package importable from inside Django.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-vpn-watchdog-dev-key-change-me",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get(
        "DJANGO_ALLOWED_HOSTS",
        "127.0.0.1,localhost,.vercel.app,.now.sh",
    ).split(",")
    if h.strip()
]

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "DJANGO_CSRF_TRUSTED",
        "https://*.vercel.app,https://*.now.sh",
    ).split(",")
    if o.strip()
]


INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "monitor",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "dashboard.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "dashboard.wsgi.application"

# I don't actually use the DB; the dashboard just reads files. SQLite
# in-memory keeps Django happy.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIMEZONE", "UTC")
USE_I18N = False
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Serve static files straight from STATICFILES_DIRS via Whitenoise's
# finders, so I don't need a `collectstatic` build step on Vercel.
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Watchdog-specific knobs surfaced to the views layer.
# ---------------------------------------------------------------------------

VPN_WATCHDOG_DEMO = os.environ.get("VPN_WATCHDOG_DEMO", "0") == "1"
VPN_WATCHDOG_SAMPLE_DIR = PROJECT_ROOT / "sample_data"
VPN_WATCHDOG_LIVE_PROBE = os.environ.get("VPN_WATCHDOG_LIVE_PROBE", "0") == "1"
