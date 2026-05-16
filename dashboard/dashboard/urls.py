"""Top-level URL config — everything is mounted under the monitor app."""

from __future__ import annotations

from django.urls import include, path


urlpatterns = [
    path("", include("monitor.urls")),
]
