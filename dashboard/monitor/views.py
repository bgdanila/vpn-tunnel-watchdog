"""Three tiny views:

* `/`            — the HTML dashboard.
* `/api/state`   — JSON payload polled every 5s by the front-end.
* `/healthz`     — plain-text health check (Vercel + uptime probes).
"""

from __future__ import annotations

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render

from .services import collect_dashboard_payload


def index(request):
    payload = collect_dashboard_payload()
    return render(request, "monitor/index.html", {"payload": payload})


def api_state(request):
    payload = collect_dashboard_payload()
    return JsonResponse(payload)


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")
