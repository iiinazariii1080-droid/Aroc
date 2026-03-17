"""Prometheus metric registry for the camera stack.

All metric objects are defined here so that both the route layer
(``app/routes/metrics.py``) and the service layer can import from a
single, dependency-free module without creating a services→routes
circular dependency.
"""
from __future__ import annotations

import threading

from prometheus_client import Counter, Gauge, Histogram

# ── Gauges (current state) ──────────────────────────────────────────

system_mode = Gauge(
    "camstack_system_mode",
    "Current system mode (0=NOMINAL,1=DEGRADED,2=LOCAL_ONLY,3=SAFE)",
)

recovery_ladder_level = Gauge(
    "camstack_recovery_ladder_level",
    "Current recovery ladder level (0‑4)",
)

stream_active = Gauge(
    "camstack_stream_active",
    "Whether latest watchdog check found active stream (0/1)",
)

janus_reachable = Gauge(
    "camstack_janus_reachable",
    "Whether Janus REST API is reachable (0/1)",
)

video_age_ms = Gauge(
    "camstack_video_age_ms",
    "Age in ms of latest video frame seen by watchdog (-1 if unknown)",
)

cpu_temp_celsius = Gauge(
    "camstack_cpu_temp_celsius",
    "SoC temperature in degrees Celsius (-1 if unavailable)",
)

# ── Counters (cumulative) ───────────────────────────────────────────

watchdog_checks_total = Counter(
    "camstack_watchdog_checks_total",
    "Total watchdog check cycles",
)

watchdog_healthy_total = Counter(
    "camstack_watchdog_healthy_total",
    "Watchdog cycles that found a healthy stream",
)

watchdog_escalations_total = Counter(
    "camstack_watchdog_escalations_total",
    "Recovery ladder escalations triggered by watchdog",
    ["level"],
)

fdir_events_total = Counter(
    "camstack_fdir_events_total",
    "FDIR events emitted",
    ["domain", "severity"],
)

fdir_events_dropped_total = Counter(
    "camstack_fdir_events_dropped_total",
    "FDIR events silently dropped because the ring buffer (maxlen=500) was full",
)

mode_transitions_total = Counter(
    "camstack_mode_transitions_total",
    "System mode transitions",
    ["from_mode", "to_mode"],
)

ice_connects_total = Counter(
    "camstack_ice_connects_total",
    "Client ICE connection events (reported via /telemetry)",
)

legacy_endpoint_hits_total = Counter(
    "camstack_legacy_endpoint_hits_total",
    "Requests to deprecated endpoint aliases (use to track migration progress)",
    ["endpoint"],
)

# ── Histograms ──────────────────────────────────────────────────────

ice_connect_duration_seconds = Histogram(
    "camstack_ice_connect_duration_seconds",
    "ICE connection time as reported by client getStats()",
    buckets=[0.5, 1, 2, 3, 5, 8, 10, 15, 30],
)

ttff_seconds = Histogram(
    "camstack_ttff_seconds",
    "Time-to-first-frame as reported by client telemetry",
    buckets=[1, 2, 3, 5, 8, 10, 15, 20, 30],
)

client_packet_loss_ratio = Gauge(
    "camstack_client_packet_loss_ratio",
    "Latest client-reported packet loss ratio (0.0-1.0)",
)

# Named without _total suffix: this is a Gauge (client-reported cumulative
# value reset on reconnect), not a server-side Counter.  Using _total would
# mislead operators into applying rate()/increase() which produce nonsense
# on a Gauge that resets when the client reconnects.
client_frames_decoded = Gauge(
    "camstack_client_frames_decoded",
    "Latest client-reported cumulative framesDecoded",
)

# Records the Unix epoch of the last telemetry report so operators can
# compute staleness in PromQL: time() - camstack_client_last_report_ts
# A value of 0 means no report has been received since service start.
# Replaces the previous camstack_client_last_report_age_seconds which was
# always 0 (set to 0 on receipt, never incremented afterward).
client_last_report_ts = Gauge(
    "camstack_client_last_report_ts",
    "Unix timestamp of the most recent client telemetry stats_report (0 = never)",
)

ratelimit_tracked_buckets = Gauge(
    "camstack_ratelimit_tracked_buckets",
    "Number of (path, client_ip) pairs currently tracked by the rate limiter",
)

thermal_monitor_heartbeat = Gauge(
    "camstack_thermal_monitor_heartbeat",
    "Unix timestamp of last thermal monitor loop iteration (0 = never started)",
)

watchdog_heartbeat = Gauge(
    "camstack_watchdog_heartbeat",
    "Unix timestamp of last watchdog loop iteration (0 = never started)",
)

# ── State mirrors (plain Python, updated alongside Gauge.set()) ─────
# Read these in health-check code instead of the private Gauge._value API.
# Protected by _client_state_lock so readers always see a consistent snapshot.
_client_state_lock = threading.Lock()
_client_frames_decoded_last: float = 0.0
_client_packet_loss_last: float = 0.0
_client_report_ts_last: float = 0.0   # epoch seconds of last stats_report; 0 = never


def get_client_state() -> dict:
    """Return a consistent snapshot of the latest client telemetry state.

    Thread-safe.  Returns zeros when no telemetry has been received yet.
    """
    with _client_state_lock:
        return {
            "frames_decoded": _client_frames_decoded_last,
            "packet_loss_ratio": _client_packet_loss_last,
            "report_ts_last": _client_report_ts_last,
        }


def update_client_state(
    frames_decoded: float | None = None,
    packet_loss_ratio: float | None = None,
    report_ts: float | None = None,
) -> None:
    """Update client telemetry state atomically under lock.

    Keeps Prometheus gauges and the Python mirrors in sync.  Callers
    (e.g. the telemetry route) must use this instead of accessing the
    private lock and mirror variables directly.
    """
    global _client_frames_decoded_last, _client_packet_loss_last, _client_report_ts_last
    with _client_state_lock:
        if frames_decoded is not None:
            _client_frames_decoded_last = frames_decoded
            client_frames_decoded.set(frames_decoded)
        if packet_loss_ratio is not None:
            _client_packet_loss_last = packet_loss_ratio
            client_packet_loss_ratio.set(packet_loss_ratio)
        if report_ts is not None:
            _client_report_ts_last = report_ts
            client_last_report_ts.set(report_ts)
