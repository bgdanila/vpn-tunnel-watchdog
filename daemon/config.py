"""Settings for the watchdog.

Everything tunable lives here so I don't have to hunt through the code
when I want to change a path or a threshold. All values can be overridden
through environment variables, which is what the launchd plist uses on
the real install and what `vercel.json` uses for the demo deploy.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env(key: str, default: str) -> str:
    value = os.environ.get(key)
    return value if value not in (None, "") else default


# ---------------------------------------------------------------------------
# WireGuard interface + config file
# ---------------------------------------------------------------------------

# macOS picks the next free utun slot when wg-quick brings the tunnel up,
# so this might need to be changed (utun3, utun4, ...). Check with
# `sudo wg show interfaces`.
WG_INTERFACE: str = _env("VPN_WATCHDOG_IFACE", "utun3")

# Path I pass to `wg-quick up/down`.
WG_CONF_PATH: str = _env(
    "VPN_WATCHDOG_CONF",
    "/usr/local/etc/wireguard/wg0.conf",
)

# ---------------------------------------------------------------------------
# Watchdog timing (all in seconds)
# ---------------------------------------------------------------------------

# How often the daemon wakes up and probes. Each cycle spawns a few
# short-lived subprocesses (ifconfig, netstat, wg show, ps) — totally
# fine at 60s; the previous "DDoS yourself" symptom came from
# unbounded recovery (wg-quick down/up) every cycle, not from probing
# itself. RECOVERY_COOLDOWN_SEC + MAX_RECOVERIES_PER_HOUR below cap
# the actual destructive work.
PROBE_INTERVAL_SEC: int = int(_env("VPN_WATCHDOG_INTERVAL", "60"))

# Handshake newer than this -> healthy.
HEALTHY_HANDSHAKE_SEC: int = int(_env("VPN_WATCHDOG_HEALTHY", "120"))

# Handshake older than this -> stalled (the "ghost tunnel" case).
STALLED_HANDSHAKE_SEC: int = int(_env("VPN_WATCHDOG_STALLED", "180"))

# Pause between `wg-quick down` and `wg-quick up` so the kernel has time
# to actually tear the iface down before we ask it to come back up.
RESTART_PAUSE_SEC: float = float(_env("VPN_WATCHDOG_RESTART_PAUSE", "2"))

# Minimum seconds between two recovery attempts. Without this the
# daemon happily runs wg-quick down/up every probe cycle, which on
# macOS leaks one wireguard-go process per cycle and keeps tearing the
# user's network down.
RECOVERY_COOLDOWN_SEC: int = int(_env("VPN_WATCHDOG_RECOVERY_COOLDOWN", "600"))

# Hard cap on recovery attempts within a rolling hour. Acts as a
# circuit breaker when the tunnel is permanently broken (bad config,
# wrong VPN_WATCHDOG_IFACE, server down) so we stop hammering wg-quick
# and let the user investigate instead.
MAX_RECOVERIES_PER_HOUR: int = int(_env("VPN_WATCHDOG_MAX_RECOVERIES", "5"))

# ---------------------------------------------------------------------------
# Files we write to
# ---------------------------------------------------------------------------

# Main log file. Dashboard tails this for the "terminal" widget.
LOG_FILE: str = _env("VPN_WATCHDOG_LOG", "/var/log/vpn_watchdog.log")

# JSON snapshot of the latest probe result, also read by the dashboard.
STATE_FILE: str = _env("VPN_WATCHDOG_STATE", "/var/log/vpn_watchdog_state.json")

# Per-day intervention counter (small JSON blob).
COUNTER_FILE: str = _env("VPN_WATCHDOG_COUNTER", "/var/log/vpn_watchdog_counter.json")

# ---------------------------------------------------------------------------
# Where the OS commands live
# ---------------------------------------------------------------------------

# Defaults assume Apple Silicon Homebrew. On Intel use /usr/local/bin/.
WG_BIN: str = _env("VPN_WATCHDOG_WG", "/opt/homebrew/bin/wg")
WG_QUICK_BIN: str = _env("VPN_WATCHDOG_WGQUICK", "/opt/homebrew/bin/wg-quick")
IFCONFIG_BIN: str = _env("VPN_WATCHDOG_IFCONFIG", "/sbin/ifconfig")
PS_BIN: str = _env("VPN_WATCHDOG_PS", "/bin/ps")


def _candidate_paths(path: str) -> list[Path]:
    return [
        Path(path),
        Path.home() / ".vpn_watchdog" / Path(path).name,
        Path(__file__).resolve().parent.parent / "local_logs" / Path(path).name,
    ]


def ensure_writable(path: str) -> Path:
    """Pick a path I can actually open for writing.

    Tries the configured location first, then `~/.vpn_watchdog/`, then a
    project-local `local_logs/` folder. That way the dashboard and the
    daemon both work whether they run as root (via launchd) or as my
    normal user during development, without crashing on `/var/log`.
    """
    for candidate in _candidate_paths(path):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            if candidate.exists():
                if os.access(candidate, os.W_OK):
                    return candidate
                # Already exists but owned by someone else (typical after a
                # previous sudo run created the file). Skip to the fallback.
                continue
            # Try to actually open it for append; that's the only way to be
            # sure we can write — `touch()` quietly succeeds even when we
            # can't.
            with open(candidate, "ab"):
                pass
            return candidate
        except (PermissionError, OSError):
            continue
    return Path(path)


def resolve_for_read(path: str) -> Path:
    """Pick the most authoritative readable copy of `path`.

    Different from `ensure_writable`: the dashboard usually runs as the
    unprivileged user while the daemon runs as root via launchd, so the
    real state file is at /var/log/... owned by root. The user can read
    it but not write it — `ensure_writable` would skip it and fall back
    to an empty file in `~/.vpn_watchdog/`, which is exactly the bug
    that left the dashboard stuck on "Awaiting first probe".

    Picks the newest *existing* readable candidate so we always show
    fresh data regardless of which side ran last.
    """
    best: Path | None = None
    best_mtime = -1.0
    for candidate in _candidate_paths(path):
        try:
            if candidate.exists() and os.access(candidate, os.R_OK):
                mtime = candidate.stat().st_mtime
                if mtime > best_mtime:
                    best_mtime = mtime
                    best = candidate
        except OSError:
            continue
    return best if best is not None else Path(path)
