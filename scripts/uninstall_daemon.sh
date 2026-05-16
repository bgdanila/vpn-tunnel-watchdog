#!/usr/bin/env bash
#
# Stop the watchdog and remove its LaunchDaemon manifest.
#
# Usage:
#   sudo ./scripts/uninstall_daemon.sh

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: this script must be run as root (use sudo)." >&2
    exit 1
fi

LABEL="com.micudanila.vpnwatchdog"
DEST_PLIST="/Library/LaunchDaemons/${LABEL}.plist"

if launchctl print "system/${LABEL}" >/dev/null 2>&1; then
    echo "==> Stopping ${LABEL}"
    launchctl bootout "system/${LABEL}" || true
fi

if [[ -f "${DEST_PLIST}" ]]; then
    echo "==> Removing ${DEST_PLIST}"
    rm -f "${DEST_PLIST}"
fi

echo "==> Uninstalled. Log files in /var/log/vpn_watchdog*.log were kept."
