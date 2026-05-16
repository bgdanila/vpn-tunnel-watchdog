#!/usr/bin/env bash
#
# Tear down the WireGuard test stack created by `test_stack_up.sh`.
#
# Usage:
#   ./scripts/test_stack_down.sh           # stop tunnel + container, keep config files
#   ./scripts/test_stack_down.sh --purge   # also delete wg0.conf and ~/wg-test/

set -euo pipefail

CONTAINER="${WG_TEST_CONTAINER:-wg-test}"
HOST_DIR="${WG_TEST_HOST_DIR:-${HOME}/wg-test}"
IMAGE="${WG_TEST_IMAGE:-lscr.io/linuxserver/wireguard:latest}"
WG_CONF_DEST="${VPN_WATCHDOG_CONF:-/usr/local/etc/wireguard/wg0.conf}"

WG_BIN="${VPN_WATCHDOG_WG:-/opt/homebrew/bin/wg}"
WG_QUICK_BIN="${VPN_WATCHDOG_WGQUICK:-/opt/homebrew/bin/wg-quick}"

PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

# ---------------------------------------------------------------------------
# Down the tunnel if it's up
# ---------------------------------------------------------------------------

if sudo "${WG_BIN}" show interfaces 2>/dev/null | grep -q .; then
    if [[ -f "${WG_CONF_DEST}" ]]; then
        echo "==> wg-quick down ${WG_CONF_DEST}"
        sudo "${WG_QUICK_BIN}" down "${WG_CONF_DEST}" || true
    else
        IFACE="$(sudo "${WG_BIN}" show interfaces | awk '{print $1; exit}')"
        echo "==> wg-quick down ${IFACE}"
        sudo "${WG_QUICK_BIN}" down "${IFACE}" || true
    fi
fi

# ---------------------------------------------------------------------------
# Stop + remove the docker container
# ---------------------------------------------------------------------------

if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER}$"; then
    echo "==> Stopping container ${CONTAINER}"
    # Cap the wait so a wedged container can't keep us hanging.
    docker stop -t 5 "${CONTAINER}" >/dev/null 2>&1 || true
    # `docker rm -v` also drops the anonymous volumes the container
    # created, so they don't pile up in Docker Desktop's VM disk.
    docker rm -v "${CONTAINER}" >/dev/null 2>&1 || true
fi

# ---------------------------------------------------------------------------
# Optional cleanup
# ---------------------------------------------------------------------------

if [[ "${PURGE}" -eq 1 ]]; then
    echo "==> Purging ${WG_CONF_DEST} and ${HOST_DIR}"
    sudo rm -f "${WG_CONF_DEST}"
    rm -rf "${HOST_DIR}"

    # Drop the cached image too — otherwise repeated test cycles leave
    # ~150 MB per pull rotting in Docker's VM. Ignore failures (image
    # might already be gone, or pinned by another container).
    if docker image inspect "${IMAGE}" >/dev/null 2>&1; then
        echo "==> Removing cached image ${IMAGE}"
        docker rmi "${IMAGE}" >/dev/null 2>&1 || true
    fi
fi

echo "==> Test stack is down."
