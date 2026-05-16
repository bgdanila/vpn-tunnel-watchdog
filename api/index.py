"""Vercel entry point for the dashboard.

Vercel's @vercel/python runtime auto-discovers files under `api/`. Each
module needs to expose either a `handler` (BaseHTTPRequestHandler) or,
for a WSGI app, an `app` callable. I just re-export the Django WSGI
application here, so every URL (`/`, `/api/state`, `/healthz`, …) is
served by Django.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"

for entry in (str(PROJECT_ROOT), str(DASHBOARD_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

# Force demo mode on Vercel — the function obviously can't reach my
# Mac's WireGuard tunnel, so it serves the bundled sample data instead.
os.environ.setdefault("VPN_WATCHDOG_DEMO", "1")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dashboard.settings")
os.environ.setdefault("DJANGO_DEBUG", "0")

from dashboard.wsgi import application  # noqa: E402

app = application
handler = application
