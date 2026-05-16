#!/usr/bin/env bash
#
# One-button starter for the whole monitoring stack.
#
# Brings up, in order:
#   1. The Docker WireGuard test server   (scripts/test_stack_up.sh)
#   2. The watchdog LaunchDaemon          (scripts/install_daemon.sh)
#   3. The Django dashboard, in the bg    (scripts/run_dashboard.sh)
#
# Anything already running is left alone — the script is safe to re-run.
#
# Usage:
#   ./scripts/start_all.sh                 # default bind 127.0.0.1:8000
#   ./scripts/start_all.sh 0.0.0.0:9000    # custom dashboard bind
#   ./scripts/start_all.sh --no-dashboard  # skip the dashboard
#   ./scripts/start_all.sh --no-daemon     # skip installing the daemon
#   ./scripts/start_all.sh --no-stack      # skip the docker test stack
#
# Stop everything with: ./scripts/stop_all.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_DIR="${PROJECT_DIR}/local_logs"
DASHBOARD_PID_FILE="${RUNTIME_DIR}/dashboard.pid"
DASHBOARD_LOG_FILE="${RUNTIME_DIR}/dashboard.log"

WANT_STACK=1
WANT_DAEMON=1
WANT_DASHBOARD=1
DASHBOARD_BIND="127.0.0.1:8000"

for arg in "$@"; do
    case "${arg}" in
        --no-stack)     WANT_STACK=0     ;;
        --no-daemon)    WANT_DAEMON=0    ;;
        --no-dashboard) WANT_DASHBOARD=0 ;;
        -h|--help)
            sed -n '2,20p' "$0"
            exit 0
            ;;
        *) DASHBOARD_BIND="${arg}" ;;
    esac
done

mkdir -p "${RUNTIME_DIR}"

note() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Docker test stack
# ---------------------------------------------------------------------------

ensure_docker_running() {
    # Already up? Done.
    if docker info >/dev/null 2>&1; then
        return 0
    fi
    if ! command -v docker >/dev/null 2>&1; then
        warn "docker CLI not installed (brew install --cask docker)."
        return 1
    fi
    note "Docker Desktop is not running; launching it"
    # `open -ga Docker` returns immediately; the daemon needs ~10–30s
    # to be ready to answer `docker info`.
    open -ga Docker 2>/dev/null || true
    local waited=0
    local max_wait=60
    printf "    waiting for Docker"
    while ! docker info >/dev/null 2>&1; do
        sleep 2
        waited=$((waited + 2))
        printf "."
        if (( waited >= max_wait )); then
            printf "\n"
            warn "Docker still not responding after ${max_wait}s. Skipping test stack."
            return 1
        fi
    done
    printf " ready (%ss)\n" "${waited}"
    return 0
}

stack_ok=1
if [[ "${WANT_STACK}" -eq 1 ]]; then
    note "Bringing up the Docker WireGuard test stack"
    if ensure_docker_running && "${SCRIPT_DIR}/test_stack_up.sh"; then
        :
    else
        stack_ok=0
        warn "Docker test stack failed to come up. Continuing so the dashboard still starts."
        warn "Re-run \`./scripts/test_stack_up.sh\` once Docker is ready."
    fi
else
    note "Skipping docker test stack (--no-stack)"
fi

# ---------------------------------------------------------------------------
# 2. Watchdog LaunchDaemon
# ---------------------------------------------------------------------------

daemon_ok=1
if [[ "${WANT_DAEMON}" -eq 1 ]]; then
    note "Installing the watchdog LaunchDaemon (will prompt for sudo)"
    if ! sudo "${SCRIPT_DIR}/install_daemon.sh"; then
        daemon_ok=0
        warn "Daemon install failed. Continuing to dashboard."
    fi
else
    note "Skipping daemon install (--no-daemon)"
fi

# ---------------------------------------------------------------------------
# 3. Dashboard (background)
# ---------------------------------------------------------------------------

start_dashboard() {
    if [[ -f "${DASHBOARD_PID_FILE}" ]]; then
        local existing
        existing="$(cat "${DASHBOARD_PID_FILE}" 2>/dev/null || true)"
        if [[ -n "${existing}" ]] && kill -0 "${existing}" 2>/dev/null; then
            note "Dashboard already running (pid ${existing}). Skipping."
            return 0
        fi
        rm -f "${DASHBOARD_PID_FILE}"
    fi

    note "Starting dashboard in background -> http://${DASHBOARD_BIND}"
    # nohup so the dashboard survives this shell exiting; output goes
    # to local_logs/dashboard.log so we can debug later.
    nohup "${SCRIPT_DIR}/run_dashboard.sh" "${DASHBOARD_BIND}" \
        >>"${DASHBOARD_LOG_FILE}" 2>&1 &
    local pid=$!
    echo "${pid}" > "${DASHBOARD_PID_FILE}"
    # Give Django a beat to either bind or crash on a port-in-use error.
    sleep 2
    if ! kill -0 "${pid}" 2>/dev/null; then
        warn "Dashboard exited immediately. Tail ${DASHBOARD_LOG_FILE} for details."
        rm -f "${DASHBOARD_PID_FILE}"
        return 1
    fi
    echo "    pid=${pid}  log=${DASHBOARD_LOG_FILE}"
}

if [[ "${WANT_DASHBOARD}" -eq 1 ]]; then
    start_dashboard || true
else
    note "Skipping dashboard (--no-dashboard)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

note "Done."
status() { [[ "$1" -eq 1 ]] && echo "OK" || echo "FAILED"; }
cat <<EOF

  Test stack : $(status ${stack_ok})
  Daemon     : $(status ${daemon_ok})
  Dashboard  : http://${DASHBOARD_BIND}        (logs: ${DASHBOARD_LOG_FILE})

  Inspect:
    sudo wg show
    sudo launchctl print system/com.micudanila.vpnwatchdog
    tail -f /var/log/vpn_watchdog.log
    tail -f ${RUNTIME_DIR}/vpn_watchdog.log   # when daemon ran unprivileged

  Stop everything with:  ./scripts/stop_all.sh

EOF
