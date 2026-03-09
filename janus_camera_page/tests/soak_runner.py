#!/usr/bin/env python3
"""Phase 6 — Soak-test runner for camera stack.

NOT a pytest test — a standalone script that polls the live camera
node(s) for an extended period and records health metrics as JSON.

Usage:
    # 8-hour soak against color node
    python tests/soak_runner.py --node 192.168.1.10 --duration 8h

    # 24-hour soak with 30s poll interval
    python tests/soak_runner.py --node 192.168.1.10 --duration 24h --interval 30

    # Quick 10-minute smoke soak
    python tests/soak_runner.py --node 192.168.1.10 --duration 10m --interval 5

Output: JSON report written to ``soak_report_<node>_<timestamp>.json``.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("soak")

# ── Configuration ────────────────────────────────────────────────────


def _parse_duration(s: str) -> int:
    """Parse '8h', '30m', '10s' → seconds."""
    m = re.match(r"^(\d+)\s*([hms]?)$", s.strip().lower())
    if not m:
        raise ValueError(f"Invalid duration: {s!r}  (expected e.g. 8h, 30m, 600s)")
    val, unit = int(m.group(1)), m.group(2) or "s"
    return val * {"h": 3600, "m": 60, "s": 1}[unit]


@dataclass
class Sample:
    ts: float
    healthz_ok: bool = False
    healthz_ms: float = 0.0
    stream_ok: bool = False
    mode: str = "unknown"
    ladder_level: int = -1
    fdir_events_recent: int = 0
    error: str = ""


@dataclass
class SoakReport:
    node: str
    start_utc: str = ""
    end_utc: str = ""
    duration_target_s: int = 0
    duration_actual_s: float = 0.0
    interval_s: int = 10
    total_samples: int = 0
    healthy_samples: int = 0
    unhealthy_samples: int = 0
    availability_pct: float = 0.0
    max_healthz_ms: float = 0.0
    modes_observed: list = field(default_factory=list)
    max_ladder_level: int = 0
    total_fdir_events: int = 0
    samples: list = field(default_factory=list)
    pass_: bool = False  # trailing underscore to avoid 'pass' keyword


# ── Polling ──────────────────────────────────────────────────────────

def _poll(base_url: str, timeout: float = 10) -> Sample:
    """Collect one health sample from the node."""
    sample = Sample(ts=time.time())
    try:
        t0 = time.monotonic()
        r = httpx.get(f"{base_url}/healthz", timeout=timeout)
        sample.healthz_ms = round((time.monotonic() - t0) * 1000, 1)
        sample.healthz_ok = r.status_code == 200
        if r.status_code == 200:
            data = r.json()
            sample.stream_ok = data.get("stream_active", False)
    except Exception as e:
        sample.error = str(e)

    try:
        r = httpx.get(f"{base_url}/fdir/mode", timeout=5)
        if r.status_code == 200:
            sample.mode = r.json().get("mode", "unknown")
    except Exception:
        pass

    try:
        r = httpx.get(f"{base_url}/fdir/ladder", timeout=5)
        if r.status_code == 200:
            sample.ladder_level = r.json().get("current_level", -1)
    except Exception:
        pass

    try:
        r = httpx.get(f"{base_url}/fdir/events?n=5", timeout=5)
        if r.status_code == 200:
            sample.fdir_events_recent = len(r.json())
    except Exception:
        pass

    return sample


# ── Main loop ────────────────────────────────────────────────────────

def run_soak(node: str, port: int, duration_s: int, interval_s: int) -> SoakReport:
    base_url = f"http://{node}:{port}"
    report = SoakReport(
        node=node,
        duration_target_s=duration_s,
        interval_s=interval_s,
        start_utc=datetime.now(timezone.utc).isoformat(),
    )

    start = time.monotonic()
    deadline = start + duration_s
    modes_seen: set[str] = set()

    log.info("Soak started: node=%s duration=%ds interval=%ds", node, duration_s, interval_s)

    while time.monotonic() < deadline:
        sample = _poll(base_url)
        report.total_samples += 1

        if sample.healthz_ok:
            report.healthy_samples += 1
        else:
            report.unhealthy_samples += 1

        report.max_healthz_ms = max(report.max_healthz_ms, sample.healthz_ms)
        report.max_ladder_level = max(report.max_ladder_level, sample.ladder_level)
        report.total_fdir_events += sample.fdir_events_recent
        modes_seen.add(sample.mode)

        report.samples.append(asdict(sample))

        if report.total_samples % 60 == 0:
            pct = report.healthy_samples / report.total_samples * 100
            log.info(
                "Soak progress: %d samples, %.1f%% healthy, mode=%s, ladder=%d",
                report.total_samples, pct, sample.mode, sample.ladder_level,
            )

        time.sleep(interval_s)

    report.end_utc = datetime.now(timezone.utc).isoformat()
    report.duration_actual_s = round(time.monotonic() - start, 1)
    report.modes_observed = sorted(modes_seen)

    if report.total_samples > 0:
        report.availability_pct = round(
            report.healthy_samples / report.total_samples * 100, 2
        )

    # Pass criteria: ≥99% availability and never entered SAFE mode
    report.pass_ = (
        report.availability_pct >= 99.0
        and "safe" not in modes_seen
    )

    return report


def main():
    parser = argparse.ArgumentParser(description="Camera stack soak-test runner")
    parser.add_argument("--node", default="192.168.1.10", help="Target node IP")
    parser.add_argument("--port", type=int, default=8900, help="API port")
    parser.add_argument("--duration", default="8h", help="Test duration (e.g. 8h, 30m, 600s)")
    parser.add_argument("--interval", type=int, default=10, help="Poll interval in seconds")
    args = parser.parse_args()

    duration_s = _parse_duration(args.duration)
    report = run_soak(args.node, args.port, duration_s, args.interval)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    filename = f"soak_report_{args.node}_{ts}.json"
    out = asdict(report)
    # Rename pass_ → pass in output
    out["pass"] = out.pop("pass_")

    with open(filename, "w") as f:
        json.dump(out, f, indent=2)

    log.info("Report written to %s", filename)
    log.info(
        "Result: %s — availability=%.2f%% samples=%d modes=%s",
        "PASS" if report.pass_ else "FAIL",
        report.availability_pct,
        report.total_samples,
        report.modes_observed,
    )

    sys.exit(0 if report.pass_ else 1)


if __name__ == "__main__":
    main()
