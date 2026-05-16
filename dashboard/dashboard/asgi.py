"""ASGI entry point. Not used right now, but Django generates it by
default and it's nice to have if I ever switch to an async server."""

from __future__ import annotations

import os

from django.core.asgi import get_asgi_application


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dashboard.settings")

application = get_asgi_application()
