"""Probes that ask the OS what's going on.

There are three things I want to know on every cycle:

1. Does the configured `utunN` interface exist and have an IP?
   -> `ifconfig <iface>` (plus `netstat -ibn` for byte counters).
2. Is it actually a WireGuard tunnel and when did it last handshake?
   -> `sudo wg show` (and `sudo wg show interfaces` for the type check).
3. Which WireGuard processes are running right now?
   -> `ps -Ao pid,comm,args` filtered by name.

Each probe returns a small dataclass that gets serialised to JSON for
the dashboard, so I never have to deal with raw command output again
outside this file.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from . import config


def _wg_cmd(*args: str) -> list[str]:
    """Build a wg command that works both as root and as a normal user."""
    cmd = [config.WG_BIN, *args]
    if os.geteuid() == 0:
        return cmd
    return ["sudo", "-n", *cmd]


# ---------------------------------------------------------------------------
# Dataclasses returned by each probe
# ---------------------------------------------------------------------------


@dataclass
class InterfaceProbe:
    """What `ifconfig` told us about a network interface."""

    name: str
    exists: bool = False
    is_up: bool = False
    inet: Optional[str] = None
    rx_bytes: int = 0
    tx_bytes: int = 0
    raw: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class HandshakeProbe:
    """What we care about from `wg show <iface>`."""

    interface_listed: bool = False
    seconds_since_handshake: Optional[int] = None
    peer_endpoint: Optional[str] = None
    peer_pubkey: Optional[str] = None
    transfer_rx: int = 0
    transfer_tx: int = 0
    raw: str = ""
    error: Optional[str] = None
    # True when `wg show interfaces` lists this iface as a real tunnel.
    is_wireguard: bool = False
    # True when the wg userland tools aren't installed at all -> we can't
    # decide anything and recovery has to be skipped.
    tools_missing: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProcessProbe:
    """List of WireGuard-related processes from `ps`."""

    pids: list[int] = field(default_factory=list)
    rows: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Tiny subprocess helper so callers don't need try/except everywhere
# ---------------------------------------------------------------------------


def _run(cmd: list[str], timeout: int = 5) -> tuple[int, str, str]:
    """Run `cmd` and return (returncode, stdout, stderr).

    Any exception subprocess might raise gets turned into a synthetic
    return code so the rest of the daemon doesn't have to care.
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError as exc:
        return 127, "", f"command not found: {exc}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except Exception as exc:  # last-resort guard so the loop keeps going
        return 1, "", str(exc)


# ---------------------------------------------------------------------------
# ifconfig parsing
# ---------------------------------------------------------------------------

_INET_RE = re.compile(r"inet\s+(\d+\.\d+\.\d+\.\d+)")
# macOS ifconfig doesn't print byte counters by itself, so I pull them
# from `netstat -ibn` which has the columns I need.


def _bytes_from_netstat(iface: str) -> tuple[int, int]:
    """Return (rx_bytes, tx_bytes) for `iface` via `netstat -ibn`."""
    rc, out, _ = _run(["/usr/sbin/netstat", "-ibn"])
    if rc != 0:
        return 0, 0
    for line in out.splitlines():
        parts = line.split()
        if not parts or parts[0] != iface:
            continue
        # Header: Name Mtu Network Address Ipkts Ierrs Ibytes Opkts Oerrs Obytes Coll
        try:
            rx = int(parts[6])
            tx = int(parts[9])
            return rx, tx
        except (IndexError, ValueError):
            continue
    return 0, 0


def probe_interface(iface: str = config.WG_INTERFACE) -> InterfaceProbe:
    """Run `ifconfig <iface>` and pull out what we need."""
    rc, out, err = _run([config.IFCONFIG_BIN, iface])
    probe = InterfaceProbe(name=iface, raw=out or err)

    if rc != 0 or "does not exist" in (out + err).lower():
        return probe

    probe.exists = True
    probe.is_up = "status: active" in out or "UP" in out.split("\n", 1)[0]

    match = _INET_RE.search(out)
    if match:
        probe.inet = match.group(1)

    rx, tx = _bytes_from_netstat(iface)
    probe.rx_bytes = rx
    probe.tx_bytes = tx
    return probe


# ---------------------------------------------------------------------------
# wg show parsing
# ---------------------------------------------------------------------------

_HANDSHAKE_LABEL = "latest handshake:"
_ENDPOINT_LABEL = "endpoint:"
_PEER_LABEL = "peer:"
_TRANSFER_LABEL = "transfer:"

_DURATION_UNITS = {
    "second": 1,
    "seconds": 1,
    "minute": 60,
    "minutes": 60,
    "hour": 3600,
    "hours": 3600,
    "day": 86400,
    "days": 86400,
}


def _parse_duration(text: str) -> Optional[int]:
    """Turn "1 minute, 12 seconds ago" (and friends) into seconds."""
    text = text.strip().lower()
    if not text or text == "0":
        return 0
    if text.endswith("ago"):
        text = text[:-3].strip()
    total = 0
    pending = 0
    for chunk in text.replace(",", " ").split():
        if chunk.isdigit():
            pending = int(chunk)
            continue
        unit = _DURATION_UNITS.get(chunk)
        if unit is not None:
            total += pending * unit
            pending = 0
    return total or None


def _parse_transfer(text: str) -> tuple[int, int]:
    """Parse `transfer: 12.34 KiB received, 56.78 KiB sent`."""
    units = {
        "b": 1,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
    }
    rx = tx = 0
    parts = [p.strip() for p in text.split(",")]
    for part in parts:
        tokens = part.split()
        if len(tokens) < 3:
            continue
        try:
            value = float(tokens[0])
        except ValueError:
            continue
        scale = units.get(tokens[1].lower(), 1)
        amount = int(value * scale)
        if "received" in part:
            rx = amount
        elif "sent" in part:
            tx = amount
    return rx, tx


def _wg_interfaces() -> tuple[list[str], bool]:
    """Ask `wg show interfaces` for the list of *real* WireGuard tunnels.

    Returns (list_of_names, tools_missing). The flag is True when the wg
    binary itself isn't on disk — that lets the rest of the daemon stop
    looping a useless restart cycle every minute.
    """
    rc, out, err = _run(_wg_cmd("show", "interfaces"))
    combined = (out + err).lower()
    if "command not found" in combined or "no such file" in combined or rc == 127:
        return [], True
    if rc != 0:
        return [], False
    return out.split(), False


def probe_handshake(iface: str = config.WG_INTERFACE) -> HandshakeProbe:
    """Run `sudo wg show <iface>` and pull out the bits we need."""
    wg_ifaces, tools_missing = _wg_interfaces()

    rc, out, err = _run(_wg_cmd("show", iface))
    probe = HandshakeProbe(raw=out or err)
    probe.tools_missing = tools_missing
    probe.is_wireguard = iface in wg_ifaces

    if tools_missing:
        probe.error = "wireguard userland tools not installed"
        return probe

    if rc != 0:
        probe.error = err.strip() or f"wg show exited with {rc}"
        return probe

    probe.interface_listed = bool(out.strip())

    for raw_line in out.splitlines():
        line = raw_line.strip()
        lower = line.lower()
        if lower.startswith(_PEER_LABEL):
            probe.peer_pubkey = line.split(":", 1)[1].strip()
        elif lower.startswith(_ENDPOINT_LABEL):
            probe.peer_endpoint = line.split(":", 1)[1].strip()
        elif lower.startswith(_HANDSHAKE_LABEL):
            value = line.split(":", 1)[1].strip()
            probe.seconds_since_handshake = _parse_duration(value)
        elif lower.startswith(_TRANSFER_LABEL):
            value = line.split(":", 1)[1].strip()
            probe.transfer_rx, probe.transfer_tx = _parse_transfer(value)

    # `wg` shows the interface but no handshake line until the first one
    # has actually completed. Leave the field as None in that case.
    if probe.interface_listed and probe.seconds_since_handshake is None:
        probe.seconds_since_handshake = None

    return probe


# ---------------------------------------------------------------------------
# Process probe — the small "OS 2 flex" bit
# ---------------------------------------------------------------------------


def probe_processes(pattern: str = "wireguard") -> ProcessProbe:
    """Find PIDs of WireGuard-related processes via `ps`."""
    rc, out, _ = _run([config.PS_BIN, "-Ao", "pid,comm,args"])
    result = ProcessProbe()
    if rc != 0:
        return result

    needle = pattern.lower()
    for line in out.splitlines()[1:]:
        if needle in line.lower():
            tokens = line.strip().split(None, 2)
            if not tokens:
                continue
            try:
                result.pids.append(int(tokens[0]))
            except ValueError:
                continue
            result.rows.append(line.strip())
    return result


# ---------------------------------------------------------------------------
# Convenience wrapper used by the daemon main loop
# ---------------------------------------------------------------------------


def detect_wg_interface() -> Optional[str]:
    """Return the name of the WireGuard interface we should probe.

    Prefers the configured VPN_WATCHDOG_IFACE if `wg show interfaces`
    confirms it really is a WG tunnel. Otherwise falls back to whatever
    `wg show interfaces` lists (first one). On macOS wg-quick allocates
    a fresh utunN every reboot, so hard-coding utun3 in config.py is a
    foot-gun; this auto-detect makes the daemon survive that.
    """
    wg_ifaces, tools_missing = _wg_interfaces()
    if tools_missing or not wg_ifaces:
        return None
    if config.WG_INTERFACE in wg_ifaces:
        return config.WG_INTERFACE
    return wg_ifaces[0]


def collect_snapshot() -> dict:
    """Run all three probes and return them as one dict."""
    # Pick the real WG interface up front so all three probes look at
    # the same name (and the dashboard sees the actual utunN, not the
    # stale config default).
    detected = detect_wg_interface()
    actual_iface = detected or config.WG_INTERFACE
    iface = probe_interface(actual_iface)
    handshake = probe_handshake(actual_iface)
    procs = probe_processes()

    # Tell `state.evaluate` whether there's *any* WG tunnel on the box
    # right now. Without this it can't tell apart:
    #   (a) "the iface name is wrong but there IS a real WG tunnel
    #        elsewhere"   -> UNKNOWN, do not recover (would loop forever)
    #   (b) "no WG tunnel exists at all, the configured utunN happens
    #        to be a non-WG system tunnel like iCloud Private Relay"
    #                      -> DEAD, recovery should bring one up.
    # `detected` is non-None iff `wg show interfaces` returned at least
    # one tunnel, so that's exactly the discriminator.
    any_wg_interface = detected is not None

    return {
        "ts": int(time.time()),
        "interface": iface.as_dict(),
        "handshake": handshake.as_dict(),
        "processes": procs.as_dict(),
        "configured_iface": config.WG_INTERFACE,
        "active_iface": actual_iface,
        "any_wg_interface": any_wg_interface,
    }
