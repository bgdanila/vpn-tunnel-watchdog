"""WSGI entry point — used by gunicorn locally and by Vercel's runtime."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make sure both the repo root and the dashboard/ folder are importable
# regardless of how WSGI is invoked.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"
for entry in (str(PROJECT_ROOT), str(DASHBOARD_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dashboard.settings")

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()

# Vercel's @vercel/python runtime looks for `app` or `handler`.
app = application
handler = application
