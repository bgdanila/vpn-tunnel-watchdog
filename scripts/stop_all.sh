#!/usr/bin/env bash
#
# One-button teardown for the whole monitoring stack.
#
# Removes / kills every moving part start_all.sh brings up, in an
# order that actually frees the network instead of racing the daemon:
#   1. The dashboard background process     (pidfile in local_logs/)
#   2. The watchdog LaunchDaemon            (bootout + pkill + rm plist)
#   3. Stuck wg-quick processes             (the ones the daemon spawned)
#   4. Every WireGuard tunnel               (wg-quick down on each iface)
#   5. wireguard-go userspace daemons       (+ /var/run/wireguard/*)
#   6. The Docker WireGuard test container  (purges keys + cached image)
#   7. Final sanity check (warns if anything survived)
#
# Logs in local_logs/ and /var/log/vpn_watchdog*.log are wiped by
# default. Pass --keep-logs if you want to inspect them after teardown.
#
# Usage:
#   ./scripts/stop_all.sh              # stop + clean everything (incl. logs)
#   ./scripts/stop_all.sh --keep-logs  # keep local_logs/ and /var/log/vpn_watchdog*

set -uo pipefail   # not -e: this script is best-effort; one failure
                   # shouldn't stop the rest of the cleanup.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_DIR="${PROJECT_DIR}/local_logs"
DASHBOARD_PID_FILE="${RUNTIME_DIR}/dashboard.pid"

LABEL="com.micudanila.vpnwatchdog"
DEST_PLIST="/Library/LaunchDaemons/${LABEL}.plist"
WG_CONF_DEST="${VPN_WATCHDOG_CONF:-/usr/local/etc/wireguard/wg0.conf}"
WG_BIN="${VPN_WATCHDOG_WG:-/opt/homebrew/bin/wg}"
WG_QUICK_BIN="${VPN_WATCHDOG_WGQUICK:-/opt/homebrew/bin/wg-quick}"

# Default dashboard bind matches start_all.sh. Override with
# DASHBOARD_PORT=9000 ./scripts/stop_all.sh if you launched on a
# non-default port.
DASHBOARD_PORT="${DASHBOARD_PORT:-8000}"

WIPE_LOGS=1
for arg in "$@"; do
    case "${arg}" in
        --keep-logs) WIPE_LOGS=0 ;;
        # Back-compat: --logs used to mean "also wipe logs". With logs
        # now wiped by default, treat it as a no-op so old muscle
        # memory still works.
        --logs)      WIPE_LOGS=1 ;;
        -h|--help)
            sed -n '2,18p' "$0"
            exit 0
            ;;
        *) echo "Unknown option: ${arg}" >&2; exit 1 ;;
    esac
done

note() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# 1. Dashboard background process
# ---------------------------------------------------------------------------

note "Stopping dashboard"
stopped_dashboard=0

# 1a. Kill the recorded PID. Handles the common case where start_all
#     wrote a fresh pidfile.
if [[ -f "${DASHBOARD_PID_FILE}" ]]; then
    pid="$(cat "${DASHBOARD_PID_FILE}" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
        echo "    sending TERM to pid ${pid}"
        kill "${pid}" 2>/dev/null || true
        for _ in 1 2 3 4 5; do
            kill -0 "${pid}" 2>/dev/null || break
            sleep 1
        done
        if kill -0 "${pid}" 2>/dev/null; then
            echo "    still alive, sending KILL"
            kill -9 "${pid}" 2>/dev/null || true
        fi
        stopped_dashboard=1
    fi
    rm -f "${DASHBOARD_PID_FILE}"
fi

# 1b. Belt and braces: kill any stray runserver instance for this
#     project. `pkill -f` matches against the full command line.
#     Patterns are intentionally broad — Django runserver forks a
#     reloader child whose argv includes "manage.py runserver", and
#     the parent's argv may not (it's just the venv python wrapper).
if pkill -f "manage\\.py runserver" 2>/dev/null; then
    stopped_dashboard=1
    echo "    pkill matched 'manage.py runserver'"
fi
if pkill -f "${PROJECT_DIR}.*runserver" 2>/dev/null; then
    stopped_dashboard=1
    echo "    pkill matched project-path runserver"
fi

# 1c. Last resort: whoever is actually bound to the dashboard TCP port
#     is the dashboard, regardless of what its command line looks
#     like. This catches orphaned reloader children that survived
#     1a/1b. `lsof -ti` prints just the PIDs.
port_pids="$(lsof -ti "tcp:${DASHBOARD_PORT}" -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "${port_pids}" ]]; then
    echo "    killing process(es) bound to :${DASHBOARD_PORT}: ${port_pids}"
    # shellcheck disable=SC2086 # we want word-splitting for multi-pid
    kill ${port_pids} 2>/dev/null || true
    sleep 1
    port_pids="$(lsof -ti "tcp:${DASHBOARD_PORT}" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "${port_pids}" ]]; then
        echo "    still bound, sending KILL: ${port_pids}"
        # shellcheck disable=SC2086
        kill -9 ${port_pids} 2>/dev/null || true
    fi
    stopped_dashboard=1
fi

[[ "${stopped_dashboard}" -eq 1 ]] || echo "    no dashboard process running."

# ---------------------------------------------------------------------------
# 2. Watchdog LaunchDaemon  (do this FIRST so it can't keep spawning
#    fresh wg-quick processes while we're cleaning up)
# ---------------------------------------------------------------------------

note "Removing watchdog LaunchDaemon (will prompt for sudo)"

# 2a. bootout via launchctl. Try the modern API first, fall back to the
#     legacy `unload` for older systems / partially-loaded plists.
if sudo launchctl print "system/${LABEL}" >/dev/null 2>&1; then
    echo "    launchctl bootout system/${LABEL}"
    sudo launchctl bootout "system/${LABEL}" 2>/dev/null \
        || sudo launchctl unload "${DEST_PLIST}" 2>/dev/null \
        || warn "launchctl bootout/unload failed; continuing"
fi

# 2b. Kill any python -m daemon.watchdog still running. Do this BEFORE
#     touching wg-quick, otherwise the daemon's next probe spawns yet
#     another `sudo wg-quick up`.
if sudo pkill -9 -f "daemon\\.watchdog" 2>/dev/null; then
    echo "    killed lingering daemon.watchdog process(es)"
fi

# 2c. Force-delete the plist itself. The previous incident showed that
#     a manual `sudo rm` was needed when uninstall_daemon.sh didn't run
#     — now we always make sure the file is gone.
if [[ -f "${DEST_PLIST}" ]]; then
    echo "    rm ${DEST_PLIST}"
    sudo rm -f "${DEST_PLIST}" || warn "could not delete ${DEST_PLIST}"
fi
if [[ -f "${DEST_PLIST}" ]]; then
    warn "${DEST_PLIST} still present after rm — check permissions."
else
    echo "    plist gone."
fi

# ---------------------------------------------------------------------------
# 3. Stuck wg-quick processes  (the previous incident left ~10 alive)
# ---------------------------------------------------------------------------

note "Killing stuck wg-quick processes"
if sudo pkill -9 -f "wg-quick" 2>/dev/null; then
    # Give the kernel a moment to actually reap them so wg-quick down
    # below sees a clean slate.
    sleep 1
    echo "    killed wg-quick"
else
    echo "    none running."
fi

# ---------------------------------------------------------------------------
# 4. Bring every WireGuard tunnel down for real
# ---------------------------------------------------------------------------

note "Bringing WireGuard tunnels down"
if [[ -x "${WG_BIN}" ]]; then
    # Down by config path first (cleanest), then sweep any remaining
    # interfaces wg still knows about — covers the case where the
    # daemon brought up a different utun than the configured one.
    if [[ -f "${WG_CONF_DEST}" ]] && [[ -x "${WG_QUICK_BIN}" ]]; then
        echo "    wg-quick down ${WG_CONF_DEST}"
        sudo "${WG_QUICK_BIN}" down "${WG_CONF_DEST}" 2>/dev/null || true
    fi
    for iface in $(sudo "${WG_BIN}" show interfaces 2>/dev/null); do
        echo "    wg-quick down ${iface}"
        sudo "${WG_QUICK_BIN}" down "${iface}" 2>/dev/null || true
    done
else
    echo "    wg not installed; skipping."
fi

# ---------------------------------------------------------------------------
# 5. wireguard-go userspace daemons + leftover socket files
# ---------------------------------------------------------------------------

note "Killing wireguard-go and clearing /var/run/wireguard"
if sudo pkill -9 -f "wireguard-go" 2>/dev/null; then
    echo "    killed wireguard-go"
else
    echo "    none running."
fi
sudo rm -f /var/run/wireguard/*.sock /var/run/wireguard/*.name 2>/dev/null || true

# ---------------------------------------------------------------------------
# 5b. Repair routing damage from previous "full tunnel" wg-quick runs
# ---------------------------------------------------------------------------
#
# When wg-quick brings up a config with `AllowedIPs = 0.0.0.0/0` it
# installs three routes that hijack the entire IPv4 address space:
#
#   0.0.0.0/1     -> utunN
#   128.0.0.0/1   -> utunN
#   127.0.0.1     -> <LAN gateway>     # so the WG endpoint stays reachable
#
# `wg-quick down` removes them, BUT only for the iface it brought up.
# If a previous run was pkill'd mid-flight (or the iface number
# changed across reboots), these routes get orphaned and silently
# break:
#   * loopback (curl 127.0.0.1 fails with "Can't assign requested
#     address"), so the dashboard can't be reached and the WG
#     handshake to a docker-hosted server can't get back to it
#   * every outbound connection (Cursor, browsers, brew, ...) gets
#     funneled into a tunnel that no longer exists
#
# Defensively try to delete them. `route delete` errors out if the
# route doesn't exist; that's expected and ignored.
note "Repairing routing table (full-tunnel leftovers)"
sudo route -n delete -inet 0.0.0.0/1   2>/dev/null && echo "    removed 0.0.0.0/1 hijack"   || true
sudo route -n delete -inet 128.0.0.0/1 2>/dev/null && echo "    removed 128.0.0.0/1 hijack" || true
sudo route -n delete -inet 127.0.0.1   2>/dev/null && echo "    removed 127.0.0.1 override" || true

# ---------------------------------------------------------------------------
# 6. Docker test stack (with full purge)
# ---------------------------------------------------------------------------

note "Tearing down Docker test stack (--purge)"
if [[ -x "${SCRIPT_DIR}/test_stack_down.sh" ]]; then
    "${SCRIPT_DIR}/test_stack_down.sh" --purge || warn "test_stack_down.sh exited non-zero"
else
    warn "test_stack_down.sh not found; skipping container teardown"
fi

# ---------------------------------------------------------------------------
# 7. Final sanity check
# ---------------------------------------------------------------------------

note "Sanity check"
remaining_wg="$(pgrep -laf 'wg-quick|wireguard-go|daemon\\.watchdog|manage\\.py runserver' || true)"
if [[ -n "${remaining_wg}" ]]; then
    warn "Some processes are still running:"
    echo "${remaining_wg}"
else
    echo "    no wg-quick / wireguard-go / daemon.watchdog / runserver processes left."
fi
remaining_port="$(lsof -ti "tcp:${DASHBOARD_PORT}" -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "${remaining_port}" ]]; then
    warn "Something is still bound to :${DASHBOARD_PORT} (pids ${remaining_port}). Investigate with:"
    echo "        lsof -nP -iTCP:${DASHBOARD_PORT} -sTCP:LISTEN"
else
    echo "    nothing bound to :${DASHBOARD_PORT}."
fi

# ---------------------------------------------------------------------------
# 8. Optionally wipe logs
# ---------------------------------------------------------------------------

if [[ "${WIPE_LOGS}" -eq 1 ]]; then
    note "Wiping logs"
    if [[ -d "${RUNTIME_DIR}" ]]; then
        find "${RUNTIME_DIR}" -mindepth 1 -delete 2>/dev/null || true
        echo "    cleared ${RUNTIME_DIR}/"
    fi
    sudo rm -f /var/log/vpn_watchdog.log \
               /var/log/vpn_watchdog.log.* \
               /var/log/vpn_watchdog.stdout.log \
               /var/log/vpn_watchdog.stderr.log \
               /var/log/vpn_watchdog_state.json \
               /var/log/vpn_watchdog_counter.json 2>/dev/null || true
    # Also drop the user-fallback location the daemon uses when it
    # can't write to /var/log (i.e. when running unprivileged).
    rm -f "${HOME}/.vpn_watchdog/"*.log \
          "${HOME}/.vpn_watchdog/"*.log.* \
          "${HOME}/.vpn_watchdog/"*.json 2>/dev/null || true
    echo "    cleared /var/log/vpn_watchdog* and ~/.vpn_watchdog/*"
else
    echo
    echo "    (--keep-logs: local_logs/, /var/log/vpn_watchdog*, and ~/.vpn_watchdog/ left intact)"
fi

note "All stopped. Nothing should respawn on its own."
