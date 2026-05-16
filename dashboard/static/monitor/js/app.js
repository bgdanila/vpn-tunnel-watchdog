/* Dashboard front-end. No build step, no framework — just vanilla JS.
 *
 * What it does:
 *   - Polls /api/state every POLL_MS for fresh data from the daemon.
 *   - Bumps the "time since last handshake" counter every 1s on the
 *     client so the number visibly ticks up between polls.
 *   - Swaps the traffic-light colour + status copy whenever the daemon
 *     reports a different state.
 *
 * Polling cadence matches the daemon's PROBE_INTERVAL_SEC (60s by
 * default) — polling faster than the daemon writes is wasted work.
 * Override at runtime by setting window.POLL_MS before this script
 * loads.
 */

(function () {
    "use strict";

    const POLL_MS = window.POLL_MS || 15000;
    let pollInFlight = false;
    const STATUS_SUB = {
        HEALTHY:     "Interface up · recent handshake.",
        STALLED:     "Interface up but handshake is stale.",
        DEAD:        "Interface missing · restart pending.",
        UNAVAILABLE: "WireGuard tools not installed on this host.",
        UNKNOWN:     "Awaiting first probe.",
    };

    const $ = (id) => document.getElementById(id);

    let lastHandshakeSec = window.__BOOTSTRAP__?.handshake_seconds ?? null;
    let lastTick = Date.now();
    // Track when the last successful poll happened so we can flag the
    // UI as stale when the backend goes away (e.g. user ran stop_all
    // while leaving the browser tab open). Without this, the local
    // tickHandshake() keeps incrementing forever and the page LOOKS
    // live even though the daemon and dashboard are both dead.
    let lastSuccessfulPoll = null;
    let consecutivePollFailures = 0;
    // After this many failed polls in a row, mark the page as
    // disconnected. 2 = roughly POLL_MS * 2 (~30s) of total silence
    // before we cry wolf, which avoids flagging on a single network
    // blip.
    const DISCONNECT_THRESHOLD = 2;
    let isDisconnected = false;

    function fmtSeconds(secs) {
        if (secs == null) return "never";
        if (secs < 60) return `${secs}s`;
        if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
        const h = Math.floor(secs / 3600);
        const m = Math.floor((secs % 3600) / 60);
        return `${h}h ${m}m`;
    }

    function applyStatus(color, status) {
        const hero = $("hero");
        if (!hero) return;
        ["status-green", "status-yellow", "status-red", "status-gray"].forEach((c) =>
            hero.classList.remove(c)
        );
        hero.classList.add(`status-${color || "gray"}`);

        if ($("status-label")) $("status-label").textContent = status || "UNKNOWN";
        if ($("status-sub"))   $("status-sub").textContent = STATUS_SUB[status] || STATUS_SUB.UNKNOWN;
    }

    function setText(id, value, fallback = "—") {
        const el = $(id);
        if (!el) return;
        el.textContent = value == null || value === "" ? fallback : value;
    }

    function renderTerminal(lines) {
        const term = $("terminal");
        if (!term) return;
        if (!lines || lines.length === 0) {
            term.textContent =
                "No log entries yet — start the daemon with `python -m daemon.watchdog`.";
            return;
        }
        term.textContent = lines.join("\n");
        // Auto-scroll to the newest line.
        term.scrollTop = term.scrollHeight;
    }

    function setDisconnected(disconnected) {
        if (disconnected === isDisconnected) return;
        isDisconnected = disconnected;
        const hero = $("hero");
        if (hero) {
            hero.classList.toggle("status-disconnected", disconnected);
        }
        const meta = $("last-update");
        if (meta) meta.classList.toggle("disconnected", disconnected);
        if (disconnected) {
            const sub = $("status-sub");
            if (sub) {
                sub.dataset.lastSub = sub.textContent;
                sub.textContent = "Dashboard cannot reach the API — values below are stale.";
            }
        } else {
            const sub = $("status-sub");
            if (sub && sub.dataset.lastSub) {
                sub.textContent = sub.dataset.lastSub;
            }
        }
    }

    function applyPayload(p) {
        applyStatus(p.color, p.status);
        setText("vpn-ip", p.vpn_ip);
        setText("iface-name", p.interface_name);
        lastHandshakeSec = p.handshake_seconds;
        lastTick = Date.now();
        setText("handshake-counter", fmtSeconds(lastHandshakeSec));
        setText("uptime-value", p.uptime_human ?? fmtSeconds(p.uptime_seconds));
        setText("interventions-value", p.interventions_today ?? 0, 0);
        setText(
            "wg-pids",
            (p.wg_pids && p.wg_pids.length) ? p.wg_pids.join(", ") : "none detected"
        );
        setText("rx-human", p.rx_human);
        setText("tx-human", p.tx_human);
        setText("peer-endpoint", p.peer_endpoint);
        setText("peer-pubkey", p.peer_pubkey);
        renderTerminal(p.log_lines);

        const meta = $("last-update");
        if (meta) {
            const d = new Date();
            const hh = String(d.getHours()).padStart(2, "0");
            const mm = String(d.getMinutes()).padStart(2, "0");
            const ss = String(d.getSeconds()).padStart(2, "0");
            meta.textContent = `updated ${hh}:${mm}:${ss}`;
        }
    }

    async function poll() {
        // Re-entrancy guard: if the previous request hasn't returned
        // yet (slow LIVE_PROBE, network blip, suspended laptop coming
        // back), don't pile a new one on top of it. Without this, a
        // slow backend can stack up requests faster than they finish
        // and starve the rest of the user's network.
        if (pollInFlight) return;
        pollInFlight = true;
        try {
            const res = await fetch("/api/state", { cache: "no-store" });
            if (!res.ok) {
                consecutivePollFailures++;
                if (consecutivePollFailures >= DISCONNECT_THRESHOLD) setDisconnected(true);
                return;
            }
            const data = await res.json();
            consecutivePollFailures = 0;
            lastSuccessfulPoll = Date.now();
            setDisconnected(false);
            applyPayload(data);
        } catch (err) {
            console.warn("dashboard poll failed", err);
            consecutivePollFailures++;
            if (consecutivePollFailures >= DISCONNECT_THRESHOLD) setDisconnected(true);
        } finally {
            pollInFlight = false;
        }
    }

    function tickHandshake() {
        if (lastHandshakeSec == null) return;
        // Freeze the counter when we know the API is unreachable —
        // otherwise the UI keeps "ticking" forward and looks live even
        // though the backend died. The frozen value is the last good
        // one, which is also what setText already shows.
        if (isDisconnected) return;
        // Add the seconds that passed since the last poll so the counter
        // ticks up smoothly instead of only updating every 5s.
        const drift = Math.floor((Date.now() - lastTick) / 1000);
        const display = lastHandshakeSec + drift;
        setText("handshake-counter", fmtSeconds(display));
    }

    poll();
    setInterval(poll, POLL_MS);
    setInterval(tickHandshake, 1000);
})();
