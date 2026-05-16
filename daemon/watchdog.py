"""Main daemon loop.

Run it directly:

    python -m daemon.watchdog            # forever, sleeps PROBE_INTERVAL_SEC
    python -m daemon.watchdog --once     # one cycle, then exit (good for cron)

For a real install I register this as a LaunchDaemon so it starts at
boot and gets auto-restarted by launchd if it dies. See
`launchd/com.micudanila.vpnwatchdog.plist` and
`scripts/install_daemon.sh`.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from collections import deque
from typing import Deque, Optional

from . import config, probes, recovery
from .logger import (
    get_logger,
    increment_intervention,
    todays_interventions,
    write_state,
)
from .state import Status, evaluate, needs_recovery


# Flipped to False by SIGINT/SIGTERM so the loop exits cleanly.
_RUNNING = True

# Module-level recovery bookkeeping. Keeps the daemon from spamming
# wg-quick every probe cycle when the tunnel is permanently broken.
_LAST_RECOVERY_TS: float = 0.0
_RECENT_RECOVERIES: Deque[float] = deque()


def _recovery_allowed(now: float, status: Status) -> tuple[bool, Optional[str]]:
    """Return (allowed, reason_when_blocked) based on cooldown + cap.

    DEAD bypasses the cooldown: a missing tunnel is a worse outage than
    a stale handshake (which can self-heal once the peer comes back),
    so we want to bring it back immediately. The hourly cap still
    applies — that's enough to stop the daemon from thrashing if the
    recovery isn't actually working.
    """
    if status is not Status.DEAD:
        if now - _LAST_RECOVERY_TS < config.RECOVERY_COOLDOWN_SEC:
            wait = int(config.RECOVERY_COOLDOWN_SEC - (now - _LAST_RECOVERY_TS))
            return False, f"cooldown active, {wait}s remaining"

    cutoff = now - 3600
    while _RECENT_RECOVERIES and _RECENT_RECOVERIES[0] < cutoff:
        _RECENT_RECOVERIES.popleft()
    if len(_RECENT_RECOVERIES) >= config.MAX_RECOVERIES_PER_HOUR:
        return False, (
            f"circuit breaker open ({len(_RECENT_RECOVERIES)} recoveries in "
            "the last hour); fix the tunnel manually"
        )
    return True, None


def _record_recovery(now: float) -> None:
    global _LAST_RECOVERY_TS
    _LAST_RECOVERY_TS = now
    _RECENT_RECOVERIES.append(now)


def _handle_signal(signum, _frame) -> None:
    global _RUNNING
    _RUNNING = False
    get_logger().info("Received signal %s; shutting down.", signum)


def _format_handshake(secs: Optional[int]) -> str:
    if secs is None:
        return "never"
    return f"{secs}s ago"


def run_once(previous_status: Optional[Status] = None) -> Status:
    """One probe -> evaluate -> (recover) -> log cycle."""
    log = get_logger()
    snapshot = probes.collect_snapshot()
    status = evaluate(snapshot)

    iface = snapshot["interface"]
    handshake = snapshot["handshake"]

    log.info(
        "Probe: %s %s. Handshake: %s. Status: %s.",
        iface.get("name"),
        "active" if iface.get("exists") else "missing",
        _format_handshake(handshake.get("seconds_since_handshake")),
        status.value,
    )

    intervention_result = None
    if needs_recovery(status):
        now = time.time()
        allowed, reason = _recovery_allowed(now, status)
        if not allowed:
            # Skip the wg-quick cycle entirely. Logging once per probe
            # is fine — we already throttled the loop, and this is what
            # keeps the daemon from DDoSing the host with wireguard-go
            # processes when the tunnel is wedged.
            log.warning(
                "Action: tunnel %s but recovery suppressed (%s).",
                status.value,
                reason,
            )
            intervention_result = {
                "ok": False,
                "skipped": True,
                "reason": reason,
                "steps": [],
                "duration_sec": 0.0,
            }
        else:
            log.warning(
                "Action: tunnel %s -> executing wg-quick down/up cycle on %s",
                status.value,
                config.WG_CONF_PATH,
            )
            intervention_result = recovery.restart_tunnel()
            if intervention_result.get("skipped"):
                # No wg-quick on disk -> don't bump the counter, just say so
                # once per cycle so the log doesn't fill up with retries.
                log.error(
                    "Action: skipped -> %s. Install wireguard-tools or set "
                    "VPN_WATCHDOG_WGQUICK to point at the binary.",
                    intervention_result.get("reason", "wg-quick unavailable"),
                )
            else:
                _record_recovery(now)
                increment_intervention()
                for step in intervention_result["steps"]:
                    log.info(
                        "Action: %s -> rc=%s %s",
                        step["label"],
                        step["returncode"],
                        step["stderr"] or step["stdout"] or "ok",
                    )
                if intervention_result.get("ok"):
                    # The dashboard should show the post-recovery tunnel
                    # state even for --once runs.
                    time.sleep(3)
                    snapshot = probes.collect_snapshot()
                    status = evaluate(snapshot)
                    iface = snapshot["interface"]
                    handshake = snapshot["handshake"]
                    log.info(
                        "Post-recovery probe: %s %s. Handshake: %s. Status: %s.",
                        iface.get("name"),
                        "active" if iface.get("exists") else "missing",
                        _format_handshake(handshake.get("seconds_since_handshake")),
                        status.value,
                    )

    if previous_status is not None and previous_status != status:
        log.info("State change: %s -> %s", previous_status.value, status.value)

    write_state(
        {
            "status": status.value,
            "color": status.color(),
            "snapshot": snapshot,
            "interventions_today": todays_interventions(),
            "last_intervention": intervention_result,
            "thresholds": {
                "healthy": config.HEALTHY_HANDSHAKE_SEC,
                "stalled": config.STALLED_HANDSHAKE_SEC,
                "interval": config.PROBE_INTERVAL_SEC,
            },
            "wg_interface": config.WG_INTERFACE,
            "wg_conf": config.WG_CONF_PATH,
        }
    )
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VPN Status Watchdog daemon")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single probe cycle and exit (useful for cron or tests).",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=config.PROBE_INTERVAL_SEC,
        help="Sleep interval in seconds (default: %(default)s).",
    )
    args = parser.parse_args(argv)

    log = get_logger()
    log.info(
        "VPN watchdog starting (iface=%s, conf=%s, interval=%ss)",
        config.WG_INTERFACE,
        config.WG_CONF_PATH,
        args.interval,
    )

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    previous: Optional[Status] = None
    if args.once:
        run_once(previous)
        return 0

    # Refuse to spin: a 0/negative interval used to silently produce a
    # tight loop because `range(args.interval)` is empty.
    interval = max(1, args.interval)
    if interval != args.interval:
        log.warning(
            "Probe interval %s is too small; clamping to %ss.",
            args.interval,
            interval,
        )

    while _RUNNING:
        try:
            previous = run_once(previous)
        except Exception as exc:
            # Don't let one bad cycle take the daemon down.
            log.exception("Unhandled error in probe cycle: %s", exc)
        # Sleep in 1-second chunks so SIGTERM is handled quickly.
        for _ in range(interval):
            if not _RUNNING:
                break
            time.sleep(1)

    log.info("VPN watchdog exited cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
