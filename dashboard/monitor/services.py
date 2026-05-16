"""Bridge between the daemon's files and the Django views.

In **live mode** I read the JSON snapshot and the log file the daemon
writes, and (optionally) re-run the OS probes for fresh data on every
request.

In **demo mode** (`settings.VPN_WATCHDOG_DEMO`) I just serve the bundled
sample data from `sample_data/`. That's what the Vercel deploy uses,
because a serverless function obviously can't reach my Mac's WireGuard
tunnel.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from django.conf import settings


# ---------------------------------------------------------------------------
# Tiny helpers
# ---------------------------------------------------------------------------


def _format_bytes(num: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(max(num, 0))
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{num} B"


def _format_seconds(secs: int | None) -> str:
    if secs is None:
        return "never"
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


def _safe_read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text() or "{}")
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# Demo mode (Vercel)
# ---------------------------------------------------------------------------


def _demo_payload() -> dict[str, Any]:
    sample_dir: Path = settings.VPN_WATCHDOG_SAMPLE_DIR
    state = _safe_read_json(sample_dir / "state.json")
    log_lines: list[str] = []
    log_path = sample_dir / "vpn_watchdog.log"
    if log_path.exists():
        log_lines = [
            line for line in log_path.read_text().splitlines() if line
        ][-30:]
    return {"state": state, "log_lines": log_lines, "demo": True}


# ---------------------------------------------------------------------------
# Live mode (my Mac)
# ---------------------------------------------------------------------------


def _live_payload() -> dict[str, Any]:
    # Imported inside the function so the dashboard still boots even if
    # the daemon package isn't on PYTHONPATH for some reason.
    from daemon import config as wd_config
    from daemon.logger import read_log_path, read_state_path, tail
    from daemon.state import Status

    # Read-side resolvers: find the newest copy of the state/log files
    # that we have read access to. The daemon writes to /var/log when
    # running as root via launchd; an unprivileged dashboard couldn't
    # see those files when we used the writable resolver.
    state = _safe_read_json(read_state_path())
    log_lines = tail(read_log_path(), lines=30)

    if settings.VPN_WATCHDOG_LIVE_PROBE:
        # Run a fresh probe on the request thread. Handy when I want to
        # see live data without bothering to install the daemon.
        try:
            from daemon import probes
            from daemon.state import evaluate

            snapshot = probes.collect_snapshot()
            status = evaluate(snapshot)
            state = {
                **state,
                "status": status.value,
                "color": status.color(),
                "snapshot": snapshot,
                "wg_interface": wd_config.WG_INTERFACE,
                "wg_conf": wd_config.WG_CONF_PATH,
                "thresholds": {
                    "healthy": wd_config.HEALTHY_HANDSHAKE_SEC,
                    "stalled": wd_config.STALLED_HANDSHAKE_SEC,
                    "interval": wd_config.PROBE_INTERVAL_SEC,
                },
                "written_at": int(time.time()),
            }
        except Exception as exc:
            state.setdefault("errors", []).append(f"live probe failed: {exc}")

    if not state:
        state = {
            "status": Status.UNKNOWN.value,
            "color": Status.UNKNOWN.color(),
            "snapshot": {},
            "interventions_today": 0,
            "thresholds": {
                "healthy": wd_config.HEALTHY_HANDSHAKE_SEC,
                "stalled": wd_config.STALLED_HANDSHAKE_SEC,
                "interval": wd_config.PROBE_INTERVAL_SEC,
            },
            "wg_interface": wd_config.WG_INTERFACE,
            "wg_conf": wd_config.WG_CONF_PATH,
        }

    return {"state": state, "log_lines": log_lines, "demo": False}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def collect_dashboard_payload() -> dict[str, Any]:
    """Build the full dict the views/JS use to render the page."""
    raw = _demo_payload() if settings.VPN_WATCHDOG_DEMO else _live_payload()

    state = raw.get("state") or {}
    snapshot = state.get("snapshot") or {}
    iface = snapshot.get("interface") or {}
    handshake = snapshot.get("handshake") or {}
    processes = snapshot.get("processes") or {}

    secs_since = handshake.get("seconds_since_handshake")
    age = max(0, int(time.time()) - int(state.get("written_at") or time.time()))

    payload = {
        "demo": raw.get("demo", False),
        "status": state.get("status", "UNKNOWN"),
        "color": state.get("color", "gray"),
        "vpn_ip": iface.get("inet"),
        "interface_name": iface.get("name") or state.get("wg_interface"),
        "interface_exists": iface.get("exists", False),
        "rx_bytes": iface.get("rx_bytes", 0),
        "tx_bytes": iface.get("tx_bytes", 0),
        "rx_human": _format_bytes(int(iface.get("rx_bytes") or 0)),
        "tx_human": _format_bytes(int(iface.get("tx_bytes") or 0)),
        "handshake_seconds": secs_since,
        "handshake_human": _format_seconds(secs_since),
        "peer_endpoint": handshake.get("peer_endpoint"),
        "peer_pubkey": handshake.get("peer_pubkey"),
        "interventions_today": state.get("interventions_today", 0),
        "wg_pids": processes.get("pids", []),
        "ps_rows": processes.get("rows", []),
        "thresholds": state.get("thresholds", {}),
        "wg_conf": state.get("wg_conf"),
        "snapshot_age_seconds": age,
        "log_lines": raw.get("log_lines", []),
        "generated_at": int(time.time()),
    }

    # Total uptime is optional — the daemon would have to remember when it
    # last brought the tunnel up. For now, use whatever the state file
    # says (or fall back to "never").
    payload["uptime_seconds"] = state.get("uptime_seconds")
    payload["uptime_human"] = _format_seconds(payload["uptime_seconds"])
    payload["recent_interventions"] = state.get("recent_interventions", [])
    return payload
