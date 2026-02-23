"""Simple metrics for observability (reconnects, command latency, failures)."""
from typing import Dict, Any
import time

_metrics: Dict[str, Any] = {
    "reconnects_total": 0,
    "command_failures_total": 0,
    "busy_rejects_total": 0,
    "safety_violations_total": 0,
    "gripper_watchdog_releases_total": 0,
    "command_latency_ms": [],  # last N samples
    "_max_latency_samples": 100,
}


def inc_reconnects() -> None:
    _metrics["reconnects_total"] += 1


def inc_command_failures() -> None:
    _metrics["command_failures_total"] += 1


def inc_busy_rejects() -> None:
    _metrics["busy_rejects_total"] += 1


def inc_safety_violations() -> None:
    _metrics["safety_violations_total"] += 1


def inc_gripper_watchdog_releases() -> None:
    _metrics["gripper_watchdog_releases_total"] += 1


def observe_command_latency_ms(ms: float) -> None:
    lst = _metrics["command_latency_ms"]
    lst.append(ms)
    if len(lst) > _metrics["_max_latency_samples"]:
        lst.pop(0)


def get_metrics() -> Dict[str, Any]:
    return {
        "reconnects_total": _metrics["reconnects_total"],
        "command_failures_total": _metrics["command_failures_total"],
        "busy_rejects_total": _metrics["busy_rejects_total"],
        "safety_violations_total": _metrics["safety_violations_total"],
        "gripper_watchdog_releases_total": _metrics["gripper_watchdog_releases_total"],
        "command_latency_samples": len(_metrics["command_latency_ms"]),
    }
