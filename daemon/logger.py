"""Logging + the small JSON files the daemon shares with the dashboard.

I write three things on each cycle:

* Human-readable line into `vpn_watchdog.log` (rotating, ~2 MiB chunks).
* `vpn_watchdog_state.json` — the latest snapshot + status, atomically.
* `vpn_watchdog_counter.json` — `{ "YYYY-MM-DD": N }` so the dashboard
  can show how many times I had to step in today.

The dashboard never runs `wg show`; it just reads these three files.
That keeps the dashboard free of sudo/root requirements and means it
can be deployed somewhere completely separate (Vercel demo).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import time
from datetime import date
from pathlib import Path
from typing import Any

from . import config


_LOGGER_NAME = "vpn_watchdog"


def get_logger() -> logging.Logger:
    """Return the watchdog logger (set up the first time it's called)."""
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    log_path = config.ensure_writable(config.LOG_FILE)

    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
    )
    file_handler.setFormatter(
        logging.Formatter(
            fmt="[%(asctime)s] %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(file_handler)

    # Also echo to stdout so I can see what's going on when running the
    # daemon manually with `python -m daemon.watchdog`.
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(
        logging.Formatter(fmt="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(stream_handler)

    return logger


def resolved_log_path() -> Path:
    """Where the log actually ended up (after the writable fallback)."""
    return config.ensure_writable(config.LOG_FILE)


def resolved_state_path() -> Path:
    return config.ensure_writable(config.STATE_FILE)


def resolved_counter_path() -> Path:
    return config.ensure_writable(config.COUNTER_FILE)


# Read-side variants — used by the dashboard. Find the newest readable
# copy across all candidate locations instead of insisting on writable
# access. Without this, an unprivileged dashboard process can't see the
# state file the root daemon wrote to /var/log.


def read_log_path() -> Path:
    return config.resolve_for_read(config.LOG_FILE)


def read_state_path() -> Path:
    return config.resolve_for_read(config.STATE_FILE)


def read_counter_path() -> Path:
    return config.resolve_for_read(config.COUNTER_FILE)


# ---------------------------------------------------------------------------
# JSON snapshot used by the dashboard
# ---------------------------------------------------------------------------


def write_state(payload: dict[str, Any]) -> None:
    """Atomically dump `payload` into the JSON snapshot file."""
    path = resolved_state_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {**payload, "written_at": int(time.time())}
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    # rename is atomic on POSIX, so the dashboard never sees a half file.
    tmp.replace(path)


def read_state() -> dict[str, Any]:
    path = resolved_state_path()
    try:
        return json.loads(path.read_text() or "{}")
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# Daily intervention counter
# ---------------------------------------------------------------------------


def _today_key() -> str:
    return date.today().isoformat()


def read_counters() -> dict[str, int]:
    path = resolved_counter_path()
    try:
        data = json.loads(path.read_text() or "{}")
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    return {k: int(v) for k, v in data.items()}


def increment_intervention() -> int:
    """Bump today's counter and return the new value."""
    counters = read_counters()
    key = _today_key()
    counters[key] = counters.get(key, 0) + 1
    resolved_counter_path().write_text(json.dumps(counters, indent=2))
    return counters[key]


def todays_interventions() -> int:
    return read_counters().get(_today_key(), 0)


# ---------------------------------------------------------------------------
# tail() helper used by the dashboard
# ---------------------------------------------------------------------------


def tail(path: Path, lines: int = 20) -> list[str]:
    """Return the last `lines` lines of `path`.

    Reads from the end of the file in chunks instead of slurping the
    whole thing — overkill for now, but means the log can grow without
    blowing up the dashboard's memory.
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            block = 1024
            data = b""
            while size > 0 and data.count(b"\n") <= lines:
                step = min(block, size)
                size -= step
                fh.seek(size)
                data = fh.read(step) + data
        text = data.decode("utf-8", errors="replace")
        return [line for line in text.splitlines() if line][-lines:]
    except FileNotFoundError:
        return []
