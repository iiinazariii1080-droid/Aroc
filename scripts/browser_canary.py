#!/usr/bin/env python3
"""Synthetic browser canary — proves E2E video delivery.

Two modes:

1. **Browser mode** (default): Opens headless Chromium via Playwright,
   navigates to the player, waits for ICE + first decoded frame, then
   collects ``getStats()`` metrics. Requires Playwright.

2. **HTTP mode** (``--http-only``): Lightweight probe that only checks
   server-side health (``/healthz``, ``/health/stream``, ``/client-config``,
   ``/metrics``). Works anywhere with ``requests``. Designed for VPS cron.

Usage:
    # Full browser probe (LAN or external)
    python scripts/browser_canary.py --url http://192.168.1.10:8900/

    # External probe through Cloudflare (browser)
    python scripts/browser_canary.py --url https://api.techvisioncloud.pl/ \\
        --api-prefix /api/v1/color_camera --external

    # HTTP-only probe (for VPS cron without Playwright)
    python scripts/browser_canary.py --http-only \\
        --url https://api.techvisioncloud.pl/api/v1/color_camera

    # JSON output for CI
    python scripts/browser_canary.py --json --url ...

Requires:
    Browser mode: pip install playwright && playwright install chromium
    HTTP mode:    pip install requests  (usually pre-installed)

Exit code: 0 = pass, 1 = fail
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("browser_canary")

# JS snippet injected into the player page to extract WebRTC metrics.
_EXTRACT_METRICS_JS = """
async () => {
    const video = document.querySelector('video');
    if (!video) return { error: 'no video element' };

    const pcs = window.__canaryPCs || [];
    if (pcs.length === 0) {
        // Try to find PeerConnection from AutonomousPlayer namespace
        const ap = window.AutonomousPlayer;
        if (ap && ap._instance && ap._instance._streamingPort) {
            const pc = ap._instance._streamingPort.getPeerConnection();
            if (pc) pcs.push(pc);
        }
    }

    const result = {
        videoWidth: video.videoWidth,
        videoHeight: video.videoHeight,
        readyState: video.readyState,
        paused: video.paused,
    };

    if (pcs.length > 0) {
        const pc = pcs[0];
        result.iceConnectionState = pc.iceConnectionState;
        result.connectionState = pc.connectionState;

        try {
            const stats = await pc.getStats();
            stats.forEach(report => {
                if (report.type === 'inbound-rtp' && report.kind === 'video') {
                    result.framesDecoded = report.framesDecoded;
                    result.framesDropped = report.framesDropped;
                    result.packetsReceived = report.packetsReceived;
                    result.packetsLost = report.packetsLost;
                    result.bytesReceived = report.bytesReceived;
                    result.jitter = report.jitter;
                    result.framesPerSecond = report.framesPerSecond;
                }
                if (report.type === 'candidate-pair' && report.state === 'succeeded' && report.nominated) {
                    result.rttMs = (report.currentRoundTripTime || 0) * 1000;
                }
            });
        } catch (e) {
            result.statsError = String(e);
        }
    }

    return result;
}
"""

# JS to hook RTCPeerConnection creation and measure ICE + TTFF timings.
_HOOK_PEER_CONNECTION_JS = """
(() => {
    window.__canaryPCs = [];
    window.__canaryTimings = { iceStart: null, iceConnectedMs: null, firstFrameMs: null };
    const _origPC = window.RTCPeerConnection;
    window.RTCPeerConnection = function(...args) {
        const pc = new _origPC(...args);
        window.__canaryPCs.push(pc);
        window.__canaryTimings.iceStart = performance.now();
        pc.addEventListener('iceconnectionstatechange', () => {
            if ((pc.iceConnectionState === 'connected' || pc.iceConnectionState === 'completed')
                && !window.__canaryTimings.iceConnectedMs) {
                window.__canaryTimings.iceConnectedMs = performance.now() - window.__canaryTimings.iceStart;
            }
        });
        return pc;
    };
    window.RTCPeerConnection.prototype = _origPC.prototype;

    // Observe first decoded frame via video element
    const observer = new MutationObserver(() => {
        const video = document.querySelector('video');
        if (video && !video.__canaryHooked) {
            video.__canaryHooked = true;
            video.addEventListener('loadeddata', () => {
                if (!window.__canaryTimings.firstFrameMs && window.__canaryTimings.iceStart) {
                    window.__canaryTimings.firstFrameMs = performance.now() - window.__canaryTimings.iceStart;
                }
            }, { once: true });
        }
    });
    observer.observe(document, { childList: true, subtree: true });
})();
"""


def run_canary(url: str, timeout_s: float, stream_duration_s: float = 10) -> dict:
    """Run the canary probe and return metrics dict."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("playwright not installed. Run: pip install playwright && playwright install chromium")
        return {"url": url, "error": "playwright not installed", "pass": False, "duration_s": 0}

    metrics: dict = {"url": url, "pass": False}
    start = time.monotonic()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-gpu",
            ],
        )
        context = browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1280, "height": 720},
        )
        page = context.new_page()

        # Hook RTCPeerConnection before page navigates
        page.add_init_script(_HOOK_PEER_CONNECTION_JS)

        try:
            log.info("Navigating to %s", url)
            page.goto(url, timeout=int(timeout_s * 1000), wait_until="domcontentloaded")

            # Click play button if present (autoplay may be blocked)
            play_btn = page.query_selector("#playBtn")
            if play_btn:
                log.info("Clicking play button")
                play_btn.click()

            # Wait for ICE connected
            log.info("Waiting for ICE connection...")
            page.wait_for_function(
                "() => window.__canaryTimings && window.__canaryTimings.iceConnectedMs !== null",
                timeout=int(timeout_s * 1000),
            )
            timings = page.evaluate("() => window.__canaryTimings")
            metrics["ice_connect_ms"] = round(timings.get("iceConnectedMs", 0), 1)
            log.info("ICE connected in %.0f ms", metrics["ice_connect_ms"])

            # Wait for first frame
            log.info("Waiting for first frame...")
            page.wait_for_function(
                "() => { const v = document.querySelector('video'); return v && v.readyState >= 2 && v.videoWidth > 0; }",
                timeout=int(timeout_s * 1000),
            )
            timings = page.evaluate("() => window.__canaryTimings")
            ttff = timings.get("firstFrameMs")
            if ttff:
                metrics["ttff_ms"] = round(ttff, 1)
            else:
                # Fallback: measure from canary start
                metrics["ttff_ms"] = round((time.monotonic() - start) * 1000, 1)
            log.info("First frame in %.0f ms", metrics["ttff_ms"])

            # Stream for N seconds, then collect stats
            log.info("Streaming for %d s...", stream_duration_s)
            page.wait_for_timeout(int(stream_duration_s * 1000))

            stats = page.evaluate(_EXTRACT_METRICS_JS)
            metrics.update({
                "video_width": stats.get("videoWidth"),
                "video_height": stats.get("videoHeight"),
                "frames_decoded": stats.get("framesDecoded"),
                "frames_dropped": stats.get("framesDropped"),
                "packets_received": stats.get("packetsReceived"),
                "packets_lost": stats.get("packetsLost"),
                "fps": stats.get("framesPerSecond"),
                "rtt_ms": stats.get("rttMs"),
                "jitter": stats.get("jitter"),
                "ice_state": stats.get("iceConnectionState"),
            })

            # Verdicts
            frames_ok = (stats.get("framesDecoded") or 0) > 0
            ice_ok = stats.get("iceConnectionState") in ("connected", "completed")
            loss_ok = True
            if stats.get("packetsReceived") and stats.get("packetsLost") is not None:
                total = stats["packetsLost"] + stats["packetsReceived"]
                if total > 0:
                    loss_ratio = stats["packetsLost"] / total
                    metrics["packet_loss_pct"] = round(loss_ratio * 100, 2)
                    loss_ok = loss_ratio <= 0.03  # SLO: ≤ 3% alert threshold

            metrics["pass"] = frames_ok and ice_ok and loss_ok
            metrics["duration_s"] = round(time.monotonic() - start, 1)

        except Exception as exc:
            metrics["error"] = str(exc)
            metrics["duration_s"] = round(time.monotonic() - start, 1)
            log.error("Canary failed: %s", exc)
        finally:
            browser.close()

    return metrics


def run_http_probe(base_url: str, timeout_s: float = 10) -> dict:
    """HTTP-only health probe — no browser needed.

    Checks server-side endpoints to verify the stack is operational.
    Suitable for VPS cron jobs without Playwright/Chromium.
    """
    import requests

    base = base_url.rstrip("/")
    result: dict = {"url": base, "pass": False, "checks": {}}
    start = time.monotonic()

    # 1. /healthz — deep service health
    try:
        r = requests.get(f"{base}/healthz", timeout=timeout_s)
        data = r.json()
        result["checks"]["healthz"] = {
            "ok": data.get("ok", False),
            "status": r.status_code,
            "mode": data.get("mode"),
            "janus_reachable": data.get("janus_reachable"),
            "stream_active": data.get("stream_active"),
        }
    except Exception as e:
        result["checks"]["healthz"] = {"ok": False, "error": str(e)}

    # 2. /health/stream — media-level E2E
    try:
        r = requests.get(f"{base}/health/stream", timeout=timeout_s)
        data = r.json()
        result["checks"]["health_stream"] = {
            "ok": data.get("stream_usable", False),
            "status": r.status_code,
            "rtp_ingest": data.get("checks", {}).get("rtp_ingest", {}),
            "client_telemetry": data.get("checks", {}).get("client_telemetry", {}),
        }
    except Exception as e:
        result["checks"]["health_stream"] = {"ok": False, "error": str(e)}

    # 3. /client-config — TURN/ICE configuration
    try:
        r = requests.get(f"{base}/client-config", timeout=timeout_s)
        data = r.json()
        ice = data.get("iceServers", [])
        has_turn = any("turn:" in str(s.get("urls", "")) for s in ice)
        result["checks"]["client_config"] = {
            "ok": r.status_code == 200 and has_turn,
            "status": r.status_code,
            "ice_servers_count": len(ice),
            "has_turn": has_turn,
        }
    except Exception as e:
        result["checks"]["client_config"] = {"ok": False, "error": str(e)}

    # 4. /metrics — Prometheus endpoint reachable
    try:
        r = requests.get(f"{base}/metrics", timeout=timeout_s)
        has_camstack = "camstack_" in r.text
        result["checks"]["metrics"] = {
            "ok": r.status_code == 200 and has_camstack,
            "status": r.status_code,
            "has_camstack_metrics": has_camstack,
        }
    except Exception as e:
        result["checks"]["metrics"] = {"ok": False, "error": str(e)}

    result["duration_s"] = round(time.monotonic() - start, 1)

    # Verdict: all checks must pass
    all_ok = all(c.get("ok", False) for c in result["checks"].values())
    result["pass"] = all_ok

    return result


def main():
    parser = argparse.ArgumentParser(description="Synthetic browser canary for camera stack")
    parser.add_argument("--url", default="http://192.168.1.10:8900/", help="Player page URL (browser) or API base URL (http-only)")
    parser.add_argument("--timeout", type=float, default=30, help="Max wait per phase (seconds)")
    parser.add_argument("--stream-duration", type=float, default=10, help="How long to stream before collecting stats")
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    parser.add_argument("--http-only", action="store_true", help="HTTP-only probe (no browser, works on VPS)")
    parser.add_argument("--api-prefix", default="", help="API prefix for external path (e.g. /api/v1/color_camera)")
    parser.add_argument("--external", action="store_true", help="Mark as external probe (adds source label)")
    args = parser.parse_args()

    if args.json:
        logging.disable(logging.CRITICAL)

    if args.http_only:
        result = run_http_probe(args.url, args.timeout)
    else:
        result = run_canary(args.url, args.timeout, args.stream_duration)

    if args.external:
        result["source"] = "external"
    result["mode"] = "http" if args.http_only else "browser"

    print(json.dumps(result, indent=2))

    if result.get("pass"):
        log.info("CANARY PASS")
    else:
        log.error("CANARY FAIL")

    sys.exit(0 if result.get("pass") else 1)


if __name__ == "__main__":
    main()
