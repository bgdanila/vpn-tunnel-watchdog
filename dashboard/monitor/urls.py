from __future__ import annotations

from django.urls import path

from . import views


app_name = "monitor"

urlpatterns = [
    path("", views.index, name="index"),
    path("api/state", views.api_state, name="api_state"),
    path("healthz", views.healthz, name="healthz"),
]
