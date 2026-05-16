#!/usr/bin/env bash
#
# Drive the watchdog through every state on demand.
#
# Usage:
#   ./scripts/break_tunnel.sh stalled       # stop the WG server -> handshakes stall
#   ./scripts/break_tunnel.sh dead          # wg-quick down -> interface vanishes
#   ./scripts/break_tunnel.sh unavailable   # move wg-quick aside -> daemon can't recover
#   ./scripts/break_tunnel.sh restore       # put everything back to a healthy state
#
# Pair with `tail -f /var/log/vpn_watchdog.log` (or ~/.vpn_watchdog/...) to
# see the daemon react.

set -euo pipefail

CONTAINER="${WG_TEST_CONTAINER:-wg-test}"
WG_CONF_DEST="${VPN_WATCHDOG_CONF:-/usr/local/etc/wireguard/wg0.conf}"
WG_QUICK_BIN="${VPN_WATCHDOG_WGQUICK:-/opt/homebrew/bin/wg-quick}"
WG_BIN="${VPN_WATCHDOG_WG:-/opt/homebrew/bin/wg}"

mode="${1:-}"

usage() {
    cat <<EOF
Usage: $0 <stalled|dead|unavailable|restore>

  stalled      Stop the WG server container so handshakes can no longer
               complete. The watchdog flips to STALLED after the
               configured stall threshold (default ~3 min, override with
               VPN_WATCHDOG_STALLED).

  dead         Run \`wg-quick down\` so the interface disappears from
               ifconfig. Next probe should report DEAD and the daemon
               should bring it back up.

  unavailable  Temporarily move wg-quick out of the way so the daemon
               sees the tools as missing. Next probe reports UNAVAILABLE
               and recovery is skipped (no useless retries).

  restore      Reverse all of the above and return to a healthy state.
EOF
    exit 1
}

case "${mode}" in
    stalled)
        if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
            echo "ERROR: container ${CONTAINER} isn't running. Did you run test_stack_up.sh?" >&2
            exit 1
        fi
        echo "==> Stopping the WG server container (${CONTAINER})"
        docker stop "${CONTAINER}" >/dev/null
        echo "==> Done. The tunnel interface stays up but no new handshakes"
        echo "    will arrive. Watch the log: tail -f /var/log/vpn_watchdog.log"
        ;;

    dead)
        if [[ ! -f "${WG_CONF_DEST}" ]]; then
            echo "ERROR: ${WG_CONF_DEST} does not exist. Did you run test_stack_up.sh?" >&2
            exit 1
        fi
        echo "==> wg-quick down ${WG_CONF_DEST}"
        sudo "${WG_QUICK_BIN}" down "${WG_CONF_DEST}" || true
        echo "==> Interface should now be missing from ifconfig."
        ;;

    unavailable)
        if [[ ! -x "${WG_QUICK_BIN}" ]]; then
            echo "ERROR: ${WG_QUICK_BIN} not present (already unavailable?)." >&2
            exit 1
        fi
        echo "==> Moving ${WG_QUICK_BIN} -> ${WG_QUICK_BIN}.bak"
        sudo mv "${WG_QUICK_BIN}" "${WG_QUICK_BIN}.bak"
        echo "==> Done. Next probe should report UNAVAILABLE."
        echo "    Run \`./scripts/break_tunnel.sh restore\` to put it back."
        ;;

    restore)
        # 1. Put wg-quick back if we moved it.
        if [[ -f "${WG_QUICK_BIN}.bak" && ! -x "${WG_QUICK_BIN}" ]]; then
            echo "==> Restoring ${WG_QUICK_BIN}"
            sudo mv "${WG_QUICK_BIN}.bak" "${WG_QUICK_BIN}"
        fi

        # 2. Restart the docker container if needed.
        if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
            if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
                echo "==> Restarting container ${CONTAINER}"
                docker start "${CONTAINER}" >/dev/null
            fi
        else
            echo "WARN: container ${CONTAINER} doesn't exist. Run test_stack_up.sh." >&2
        fi

        # 3. Bring the tunnel back up if it's down.
        if ! sudo "${WG_BIN}" show interfaces 2>/dev/null | grep -q .; then
            if [[ -f "${WG_CONF_DEST}" ]]; then
                echo "==> wg-quick up ${WG_CONF_DEST}"
                sudo "${WG_QUICK_BIN}" up "${WG_CONF_DEST}"
            fi
        fi
        echo "==> Restore complete. Next probe should report HEALTHY."
        ;;

    *)
        usage
        ;;
esac
