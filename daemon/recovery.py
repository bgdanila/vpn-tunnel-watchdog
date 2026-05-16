"""Bring the tunnel back up via `wg-quick down` then `wg-quick up`.

Kept tiny so the daemon main loop and the tests can both call it. If the
wg-quick binary isn't even on disk we skip the whole thing instead of
spamming "command not found" once a minute.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Callable

from . import config


# Simple alias so tests can swap in a fake runner.
CommandRunner = Callable[[list[str]], subprocess.CompletedProcess]


def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _privileged_cmd(*args: str) -> list[str]:
    """Run directly as root; otherwise ask sudo to run non-interactively."""
    cmd = [*args]
    if os.geteuid() == 0:
        return cmd
    return ["sudo", "-n", *cmd]


def _wg_quick_available() -> bool:
    """True if wg-quick is on disk where config says, or anywhere in PATH."""
    if os.path.exists(config.WG_QUICK_BIN) and os.access(config.WG_QUICK_BIN, os.X_OK):
        return True
    for d in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(d, "wg-quick")
        if os.access(candidate, os.X_OK):
            return True
    return False


def restart_tunnel(
    conf_path: str = config.WG_CONF_PATH,
    pause: float = config.RESTART_PAUSE_SEC,
    runner: CommandRunner = _default_runner,
) -> dict:
    """Take the tunnel down, wait, bring it back up.

    Returns a dict describing every step. Never raises — on failure the
    `ok` flag is False and the offending step is reported. If wg-quick
    isn't installed we return early with `skipped=True`.
    """
    if not _wg_quick_available():
        return {
            "ok": False,
            "skipped": True,
            "reason": f"wg-quick not found at {config.WG_QUICK_BIN} or in PATH",
            "steps": [],
            "duration_sec": 0.0,
        }

    started = time.time()
    result: dict = {"ok": True, "skipped": False, "steps": []}

    down_cmd = _privileged_cmd(config.WG_QUICK_BIN, "down", conf_path)
    down = runner(down_cmd)
    result["steps"].append(_step("down", down))
    # `wg-quick down` legitimately fails when the tunnel is already
    # gone; don't treat that as an error.
    if down.returncode != 0 and "is not a WireGuard interface" not in (down.stderr or ""):
        result["ok"] = False

    time.sleep(pause)

    up_cmd = _privileged_cmd(config.WG_QUICK_BIN, "up", conf_path)
    up = runner(up_cmd)
    result["steps"].append(_step("up", up))
    if up.returncode != 0:
        result["ok"] = False

    result["duration_sec"] = round(time.time() - started, 2)
    return result


def _step(label: str, completed: subprocess.CompletedProcess) -> dict:
    return {
        "label": label,
        "command": " ".join(completed.args) if hasattr(completed, "args") else label,
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
    }
