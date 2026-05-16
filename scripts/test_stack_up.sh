#!/usr/bin/env bash
#
# Spin up a self-contained WireGuard test environment.
#
#   1. Start a `linuxserver/wireguard` container that acts as the server.
#   2. Wait for it to generate the peer config.
#   3. Copy that config into /usr/local/etc/wireguard/wg0.conf and rewrite
#      the endpoint so the Mac can reach it on 127.0.0.1.
#   4. Bring the tunnel up with `wg-quick`.
#
# Once this finishes you have a real WireGuard tunnel between the Mac and
# the container, with both ends under your control. Perfect for
# triggering STALLED / DEAD on demand (see `break_tunnel.sh`).
#
# Usage:
#   ./scripts/test_stack_up.sh
#
# Env overrides (all optional):
#   WG_TEST_CONTAINER   container name              (default: wg-test)
#   WG_TEST_HOST_DIR    where the server stores keys (default: ~/wg-test)
#   WG_TEST_PORT        UDP port                    (default: 51820)
#   WG_TEST_SUBNET      tunnel subnet               (default: 10.13.13.0)
#   WG_TEST_IMAGE       docker image (and tag)      (default: lscr.io/linuxserver/wireguard:latest)
#   WG_TEST_CPUS        cpu cap for the container   (default: 0.5  = half a core)
#   WG_TEST_MEMORY      memory cap                  (default: 256m)
#   WG_TEST_PIDS        max processes inside        (default: 200)
#   WG_TEST_LOG_SIZE    docker log file size        (default: 10m)
#   WG_TEST_LOG_FILES   number of rotated log files (default: 3)
#   WG_TEST_RESTART     restart policy              (default: no)
#                       Default is "no": if the container dies or you
#                       kill it, it stays dead until you re-run this
#                       script. Set e.g. "on-failure:3" if you want
#                       Docker to retry crashes for you.
#   VPN_WATCHDOG_CONF   final wg-quick config path  (default: /usr/local/etc/wireguard/wg0.conf)

set -euo pipefail

CONTAINER="${WG_TEST_CONTAINER:-wg-test}"
HOST_DIR="${WG_TEST_HOST_DIR:-${HOME}/wg-test}"
SERVER_PORT="${WG_TEST_PORT:-51820}"
SUBNET="${WG_TEST_SUBNET:-10.13.13.0}"
IMAGE="${WG_TEST_IMAGE:-lscr.io/linuxserver/wireguard:latest}"
WG_CONF_DEST="${VPN_WATCHDOG_CONF:-/usr/local/etc/wireguard/wg0.conf}"

# Resource caps. Defaults are deliberately small — this is a *test*
# WireGuard server for one peer, it has no business eating multiple
# cores or hundreds of MB. Override via env if you need more.
CPUS="${WG_TEST_CPUS:-0.5}"
MEMORY="${WG_TEST_MEMORY:-256m}"
PIDS_LIMIT="${WG_TEST_PIDS:-200}"
LOG_SIZE="${WG_TEST_LOG_SIZE:-10m}"
LOG_FILES="${WG_TEST_LOG_FILES:-3}"

# Restart policy. `unless-stopped` was a footgun: it makes Docker
# Desktop bring the container back on every login forever, which when
# combined with the watchdog daemon kept thrashing the network. Default
# is now "no" — if the container dies or is killed, it stays dead.
# Override (e.g. WG_TEST_RESTART=on-failure:3) if you want Docker to
# auto-restart on crashes.
RESTART_POLICY="${WG_TEST_RESTART:-no}"

WG_BIN="${VPN_WATCHDOG_WG:-/opt/homebrew/bin/wg}"
WG_QUICK_BIN="${VPN_WATCHDOG_WGQUICK:-/opt/homebrew/bin/wg-quick}"

die() { echo "ERROR: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------

command -v docker >/dev/null 2>&1 || die "docker not installed (brew install --cask docker)"
docker info >/dev/null 2>&1       || die "Docker isn't running. Start Docker Desktop first."

[[ -x "${WG_BIN}" ]]       || die "wg not found at ${WG_BIN} (brew install wireguard-tools)"
[[ -x "${WG_QUICK_BIN}" ]] || die "wg-quick not found at ${WG_QUICK_BIN} (brew install wireguard-tools)"

# Catch the placeholder paths from older README copies / leftover exports.
case "${WG_CONF_DEST}" in
    /path/to/*|*/your/wg0.conf|*/wg0.conf.example)
        die "VPN_WATCHDOG_CONF is set to placeholder '${WG_CONF_DEST}'. Run \`unset VPN_WATCHDOG_CONF\` and try again."
        ;;
esac
[[ "${WG_CONF_DEST}" = /* ]] || die "VPN_WATCHDOG_CONF must be an absolute path, got '${WG_CONF_DEST}'."

# ---------------------------------------------------------------------------
# Start (or reuse) the container
# ---------------------------------------------------------------------------

if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo "==> Container ${CONTAINER} already running."
elif docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo "==> Reusing existing container ${CONTAINER}."
    docker start "${CONTAINER}" >/dev/null
else
    # Reuse the cached image when it's already on disk so we don't burn
    # bandwidth re-pulling 100+ MB on every fresh run. `docker image
    # inspect` is the cheapest way to check.
    if docker image inspect "${IMAGE}" >/dev/null 2>&1; then
        echo "==> Using cached image ${IMAGE}"
    else
        echo "==> Pulling image ${IMAGE} (first run only)"
        docker pull "${IMAGE}"
    fi

    echo "==> Starting fresh container ${CONTAINER}."
    echo "    cpus=${CPUS} memory=${MEMORY} pids=${PIDS_LIMIT} restart=${RESTART_POLICY}"
    mkdir -p "${HOST_DIR}/config"
    docker run -d \
        --name="${CONTAINER}" \
        --cap-add=NET_ADMIN \
        --cap-add=SYS_MODULE \
        --cpus="${CPUS}" \
        --memory="${MEMORY}" \
        --memory-swap="${MEMORY}" \
        --pids-limit="${PIDS_LIMIT}" \
        --log-driver=json-file \
        --log-opt "max-size=${LOG_SIZE}" \
        --log-opt "max-file=${LOG_FILES}" \
        -e PEERS=1 \
        -e PEERDNS=1.1.1.1 \
        -e SERVERURL=host.docker.internal \
        -e SERVERPORT="${SERVER_PORT}" \
        -e INTERNAL_SUBNET="${SUBNET}" \
        -p "${SERVER_PORT}:${SERVER_PORT}/udp" \
        -v "${HOST_DIR}/config:/config" \
        --restart "${RESTART_POLICY}" \
        "${IMAGE}" >/dev/null
fi

# ---------------------------------------------------------------------------
# Wait for the peer config to appear
# ---------------------------------------------------------------------------

PEER_CONF="${HOST_DIR}/config/peer1/peer1.conf"
echo "==> Waiting for the container to generate ${PEER_CONF}..."
for _ in $(seq 1 60); do
    [[ -f "${PEER_CONF}" ]] && break
    sleep 1
done
[[ -f "${PEER_CONF}" ]] || die "Timed out waiting for ${PEER_CONF}. Check 'docker logs ${CONTAINER}'."

# ---------------------------------------------------------------------------
# Copy + rewrite the endpoint so the Mac can reach the server on 127.0.0.1
# ---------------------------------------------------------------------------

echo "==> Installing tunnel config to ${WG_CONF_DEST}"
sudo mkdir -p "$(dirname "${WG_CONF_DEST}")"
sudo cp "${PEER_CONF}" "${WG_CONF_DEST}"

# 1. Point the client at the loopback-mapped server.
sudo sed -i '' 's/host.docker.internal/127.0.0.1/' "${WG_CONF_DEST}"

# 2. Strip ListenPort from the client [Interface]. The linuxserver
# image writes ListenPort=51820 into every peer config, but that's
# the same UDP port the docker `-p 51820:51820/udp` forward is
# already bound to on the host -> wg-quick fails with
# "Address already in use" at `wg addconf`. Clients don't need a
# fixed listen port; let the kernel pick an ephemeral one.
sudo sed -i '' '/^[[:space:]]*ListenPort[[:space:]]*=/d' "${WG_CONF_DEST}"

# 3. Strip the DNS line. wg-quick on macOS implements DNS=1.1.1.1
# by calling `networksetup -setdnsservers` on EVERY network service
# (Wi-Fi, Thunderbolt Bridge, ProtonVPN, ...). That permanently
# overrides the user's DNS until the next `wg-quick down`, and breaks
# things like Cursor / corporate VPNs / split DNS. A test tunnel has
# no business doing that.
sudo sed -i '' '/^[[:space:]]*DNS[[:space:]]*=/d' "${WG_CONF_DEST}"

# 4. Replace the "full tunnel" AllowedIPs with split tunnel. The
# linuxserver peer ships `AllowedIPs = 0.0.0.0/0, ::/0`, which makes
# wg-quick install routes 0.0.0.0/1 and 128.0.0.0/1 pointing at the
# tunnel — i.e. all your internet traffic gets sucked into a loopback
# WireGuard tunnel whose far end is itself, creating a routing loop
# AND killing every other network connection on the Mac. We only
# need to reach the server's internal subnet.
sudo sed -i '' "s|^\\([[:space:]]*AllowedIPs[[:space:]]*=\\).*|\\1 ${SUBNET}/24|" "${WG_CONF_DEST}"

# 5. Add PersistentKeepalive so the tunnel stays warm. Without it,
# split-tunnel WireGuard only handshakes when an application sends
# something to 10.13.13.x. Since nothing on a normal Mac talks to
# that subnet, the handshake ages past STALLED_HANDSHAKE_SEC and the
# watchdog wastes its hourly recovery quota bouncing a perfectly
# good tunnel. 25s is the well-known WG community default — small
# enough to keep NAT pinholes open, big enough to be invisible.
if ! sudo grep -q "^[[:space:]]*PersistentKeepalive[[:space:]]*=" "${WG_CONF_DEST}"; then
    # Append inside the [Peer] block. Simpler than sed-injecting at
    # the right line number: just tack it onto the end.
    echo "PersistentKeepalive = 25" | sudo tee -a "${WG_CONF_DEST}" >/dev/null
fi

sudo chmod 600 "${WG_CONF_DEST}"
sudo chown root:wheel "${WG_CONF_DEST}"

echo "==> Final wg0.conf:"
sudo grep -E '^(Address|AllowedIPs|Endpoint|DNS|ListenPort|PersistentKeepalive)' "${WG_CONF_DEST}" | sed 's/^/    /'

# ---------------------------------------------------------------------------
# Bring the tunnel up (or leave it alone if it's already up)
# ---------------------------------------------------------------------------

wg_cleanup() {
    # Hardest reset we can do without touching the kernel: any
    # wireguard-go userspace daemon, any leftover socket / name file,
    # and any wg-quick process still racing to bring the tunnel up.
    sudo pkill -9 -f "wg-quick"      2>/dev/null || true
    sudo pkill -9 -f "wireguard-go"  2>/dev/null || true
    sudo rm -f /var/run/wireguard/*.sock /var/run/wireguard/*.name 2>/dev/null || true
    sleep 1
}

bring_tunnel_up() {
    # `wg-quick up` is noisy; capture stderr so we can react to the
    # specific "Address already in use" failure mode.
    local err
    err="$(sudo "${WG_QUICK_BIN}" up "${WG_CONF_DEST}" 2>&1)"
    local rc=$?
    echo "${err}"
    return ${rc}
}

EXISTING_IFACES="$(sudo "${WG_BIN}" show interfaces 2>/dev/null | tr -s ' \n' ' ' | xargs || true)"
if [[ -z "${EXISTING_IFACES}" ]]; then
    # On macOS wg-quick uses wireguard-go (a userspace daemon). When a
    # previous run crashed mid-bringup, or stop_all couldn't reap one
    # cleanly, the process / socket can stay alive holding the utun*
    # and the next `wg-quick up` fails with
    # "Unable to modify interface: Address already in use".
    # Reap proactively first.
    if pgrep -q -f wireguard-go; then
        echo "==> Cleaning up stale wireguard-go processes"
        wg_cleanup
    fi

    echo "==> Bringing the tunnel up"
    if ! UP_OUTPUT="$(bring_tunnel_up)"; then
        if echo "${UP_OUTPUT}" | grep -q "Address already in use"; then
            echo "==> wg-quick hit 'Address already in use'; doing a hard reset and retrying once"
            wg_cleanup
            if ! UP_OUTPUT="$(bring_tunnel_up)"; then
                echo "${UP_OUTPUT}" >&2
                cat >&2 <<EOF

ERROR: wg-quick up still failing with "Address already in use".

This usually means another process owns the UDP port the WG client
is trying to bind to. Common culprits:

  * Another wireguard tunnel already running:
        sudo wg show
  * A different VPN (Tailscale, OpenVPN, IKEv2) holding a utun:
        ifconfig | grep -A1 utun
  * The docker forward on this same port (${SERVER_PORT}/udp):
        lsof -nP -iUDP:${SERVER_PORT}

Try:  ./scripts/stop_all.sh   then re-run this script.
EOF
                die "wg-quick up failed (see above)."
            fi
        else
            echo "${UP_OUTPUT}" >&2
            die "wg-quick up failed (see error above)."
        fi
    fi
else
    echo "==> Tunnel already up: ${EXISTING_IFACES}"
fi

IFACE="$(sudo "${WG_BIN}" show interfaces | awk '{print $1; exit}')"

# Quick handshake check (give it ~10s).
echo "==> Waiting for first handshake..."
for _ in $(seq 1 15); do
    HS="$(sudo "${WG_BIN}" show "${IFACE}" latest-handshakes 2>/dev/null | awk '{print $2}')"
    if [[ -n "${HS}" && "${HS}" != "0" ]]; then
        break
    fi
    sleep 1
done

echo
echo "==================================================================="
echo " WireGuard test stack is up."
echo "-------------------------------------------------------------------"
echo "  Interface : ${IFACE}"
echo "  Config    : ${WG_CONF_DEST}"
echo "  Peer addr : 127.0.0.1:${SERVER_PORT}"
echo
echo " Tell the watchdog about it:"
echo "    export VPN_WATCHDOG_IFACE=${IFACE}"
echo "    export VPN_WATCHDOG_CONF=${WG_CONF_DEST}"
echo
echo " Then:"
echo "    sudo .venv/bin/python -m daemon.watchdog --once"
echo "    ./scripts/run_dashboard.sh"
echo
echo " Trigger failures with:"
echo "    ./scripts/break_tunnel.sh stalled     # handshake stops"
echo "    ./scripts/break_tunnel.sh dead        # interface vanishes"
echo "    ./scripts/break_tunnel.sh unavailable # wg-quick missing"
echo "    ./scripts/break_tunnel.sh restore     # back to healthy"
echo "==================================================================="
