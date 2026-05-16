#!/usr/bin/env bash
#
# Install the watchdog as a real macOS LaunchDaemon.
#
# Usage:
#   sudo ./scripts/install_daemon.sh
#
# What it does:
#   1. Substitutes the placeholders in the bundled .plist with the
#      absolute paths from this checkout (the project dir + the Python
#      interpreter, preferring the venv).
#   2. Drops it into /Library/LaunchDaemons/.
#   3. bootstrap + enable + kickstart through launchctl.

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: this script must be run as root (use sudo)." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Prefer the project venv if it exists; fall back to system python3.
if [[ -x "${PROJECT_DIR}/.venv/bin/python3" ]]; then
    PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python3"
else
    PYTHON_BIN="$(command -v python3)"
fi

LABEL="com.micudanila.vpnwatchdog"
SRC_PLIST="${PROJECT_DIR}/launchd/${LABEL}.plist"
DEST_PLIST="/Library/LaunchDaemons/${LABEL}.plist"

if [[ ! -f "${SRC_PLIST}" ]]; then
    echo "ERROR: bundled plist not found at ${SRC_PLIST}" >&2
    exit 1
fi

echo "==> Project dir : ${PROJECT_DIR}"
echo "==> Python bin  : ${PYTHON_BIN}"
echo "==> Installing  : ${DEST_PLIST}"

sed \
    -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
    -e "s|__PYTHON_BIN__|${PYTHON_BIN}|g" \
    "${SRC_PLIST}" > "${DEST_PLIST}"

chown root:wheel "${DEST_PLIST}"
chmod 644 "${DEST_PLIST}"

# Pre-create the log files so launchd doesn't crash on the first write.
touch /var/log/vpn_watchdog.log /var/log/vpn_watchdog.stdout.log /var/log/vpn_watchdog.stderr.log
chmod 644 /var/log/vpn_watchdog*.log

# Reload the daemon (boot it out first if it was already loaded).
if launchctl print "system/${LABEL}" >/dev/null 2>&1; then
    echo "==> Daemon already loaded; bootout first."
    launchctl bootout "system/${LABEL}" || true
fi

launchctl bootstrap system "${DEST_PLIST}"
launchctl enable "system/${LABEL}"
launchctl kickstart -k "system/${LABEL}"

echo "==> Installed and started ${LABEL}."
echo "    Tail logs with:  tail -f /var/log/vpn_watchdog.log"
