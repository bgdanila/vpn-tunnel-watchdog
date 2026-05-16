"""Decides if the tunnel is healthy, stalled, dead or just unavailable.

Pure function, no I/O — easy to test without root or WireGuard.
Thresholds come from `daemon.config`.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from . import config


class Status(str, Enum):
    HEALTHY = "HEALTHY"
    STALLED = "STALLED"
    DEAD = "DEAD"
    # WireGuard userland not installed — we can't recover, just report.
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"

    def color(self) -> str:
        return {
            Status.HEALTHY: "green",
            Status.STALLED: "yellow",
            Status.DEAD: "red",
            Status.UNAVAILABLE: "gray",
            Status.UNKNOWN: "gray",
        }[self]


def evaluate(snapshot: dict) -> Status:
    """Map a snapshot from `probes.collect_snapshot` to a Status."""
    iface = snapshot.get("interface", {})
    handshake = snapshot.get("handshake", {})
    # Defaults to True so older snapshots (pre-`any_wg_interface`) keep
    # the original behaviour of trusting the iface check below.
    any_wg = snapshot.get("any_wg_interface", True)

    # No WG tools at all -> nothing we can do, don't pretend the tunnel
    # is broken.
    if handshake.get("tools_missing"):
        return Status.UNAVAILABLE

    # `utunN` doesn't exist in ifconfig -> tunnel is gone.
    if not iface.get("exists"):
        return Status.DEAD

    # No WireGuard tunnels exist anywhere on the system. The fact that
    # the probed `utunN` exists doesn't help — it's a non-WG system
    # tunnel (e.g. iCloud Private Relay, an old utun left around by
    # macOS) that just happens to share the configured name. From the
    # watchdog's point of view this is the same as "the tunnel is
    # gone" and recovery (wg-quick up) is exactly the right action.
    if not any_wg:
        return Status.DEAD

    # `utunN` exists, there ARE WG tunnels on the system, but this
    # specific iface isn't one of them -> the user pointed
    # VPN_WATCHDOG_IFACE at the wrong utun. Recovery would just spin
    # up yet another tunnel without touching the existing one and the
    # next cycle would do the same thing — endlessly cycling the
    # user's network and leaking wireguard-go processes. Surface as
    # UNKNOWN so the dashboard shows it but recovery stays off until
    # the user fixes the iface name.
    if not handshake.get("is_wireguard"):
        return Status.UNKNOWN

    secs: Optional[int] = handshake.get("seconds_since_handshake")
    if secs is None:
        # iface is up and is WG, but no handshake recorded yet -> stalled.
        return Status.STALLED

    if secs <= config.HEALTHY_HANDSHAKE_SEC:
        return Status.HEALTHY
    if secs >= config.STALLED_HANDSHAKE_SEC:
        return Status.STALLED
    # Between the two thresholds: don't flap, call it healthy.
    return Status.HEALTHY


def needs_recovery(status: Status) -> bool:
    return status in (Status.STALLED, Status.DEAD)
