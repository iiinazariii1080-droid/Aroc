"""CPU thermal monitor with automatic FPS de-rating.

Reads ``/sys/class/thermal/thermal_zone0/temp`` (BCM2835 on Pi 5) and
adjusts the streaming profile when the SoC crosses temperature thresholds.

Thresholds (configurable via env):
    THERMAL_WARN_C   = 70  → degrade to low-fps profile
    THERMAL_CRIT_C   = 80  → stop pipeline, switch to SAFE mode
    THERMAL_RESUME_C = 65  → resume normal profile (hysteresis)

The monitor runs in a daemon thread started by ``start_thermal_monitor()``.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from app.services.fdir_events import Domain, Severity, emit, RecoveryAction
from app.services import system_mode
from app.services.system import atomic_write_text

logger = logging.getLogger("thermal")

# ── Configuration ────────────────────────────────────────────────────
THERMAL_ZONE = Path(os.getenv("THERMAL_ZONE_PATH", "/sys/class/thermal/thermal_zone0/temp"))
POLL_INTERVAL = int(os.getenv("THERMAL_POLL_SEC", "10"))
WARN_C = float(os.getenv("THERMAL_WARN_C", "70"))
CRIT_C = float(os.getenv("THERMAL_CRIT_C", "80"))
RESUME_C = float(os.getenv("THERMAL_RESUME_C", "65"))

# Profile file that ffmpeg / realsense_mux reads to decide FPS
FPS_PROFILE_PATH = Path(os.getenv("FPS_PROFILE_PATH", "/run/camera/fps_profile"))

PROFILE_NORMAL = "normal"     # e.g. 15 FPS
PROFILE_LOW = "low"           # e.g. 5 FPS
PROFILE_STOP = "stop"         # pipeline should halt

# Stop signal for the thermal monitor thread — set during shutdown.
_stop_event = threading.Event()


def read_cpu_temp() -> float | None:
    """Read CPU temperature in °C. Returns None if unavailable."""
    try:
        raw = THERMAL_ZONE.read_text().strip()
        return int(raw) / 1000.0
    except PermissionError:
        logger.error("Permission denied reading %s — thermal monitoring degraded", THERMAL_ZONE)
        return None
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Unexpected error reading CPU temperature: %s", exc)
        return None


def set_fps_profile(profile: str) -> None:
    """Write the desired FPS profile to a well-known path.

    The streaming pipeline (ffmpeg wrapper / realsense_mux) polls this
    file and adjusts capture parameters accordingly.
    """
    try:
        atomic_write_text(FPS_PROFILE_PATH, profile + "\n")
        logger.info("FPS profile set to: %s", profile)
    except Exception as exc:
        logger.warning("Could not write FPS profile: %s", exc)


def _thermal_loop() -> None:
    """Main thermal monitoring loop (runs in daemon thread)."""
    current_profile = PROFILE_NORMAL

    while not _stop_event.is_set():
        temp = read_cpu_temp()
        if temp is None:
            _stop_event.wait(POLL_INTERVAL)
            continue

        # Export temp to Prometheus if available
        try:
            from app.metrics import cpu_temp_celsius
            cpu_temp_celsius.set(temp)
        except Exception:
            pass

        if temp >= CRIT_C and current_profile != PROFILE_STOP:
            logger.critical("CPU temp %.1f°C ≥ %s°C → STOPPING pipeline", temp, CRIT_C)
            set_fps_profile(PROFILE_STOP)
            current_profile = PROFILE_STOP
            system_mode.transition(system_mode.SystemMode.SAFE, f"thermal_critical_{temp:.0f}C")
            emit(
                domain=Domain.SYSTEM,
                severity=Severity.CRITICAL,
                detection_signal=f"cpu_temp={temp:.1f}C",
                recovery_action=RecoveryAction.DEGRADE_PROFILE,
                outcome=f"pipeline stopped (thermal critical {temp:.1f}°C)",
            )

        elif temp >= WARN_C and current_profile == PROFILE_NORMAL:
            logger.warning("CPU temp %.1f°C ≥ %s°C → LOW FPS profile", temp, WARN_C)
            set_fps_profile(PROFILE_LOW)
            current_profile = PROFILE_LOW
            system_mode.degrade(f"thermal_warn_{temp:.0f}C")
            emit(
                domain=Domain.SYSTEM,
                severity=Severity.WARN,
                detection_signal=f"cpu_temp={temp:.1f}C",
                recovery_action=RecoveryAction.DEGRADE_PROFILE,
                outcome=f"switched to low FPS profile (thermal warn {temp:.1f}°C)",
            )

        elif temp <= RESUME_C and current_profile != PROFILE_NORMAL:
            logger.info("CPU temp %.1f°C ≤ %s°C → resuming NORMAL profile", temp, RESUME_C)
            set_fps_profile(PROFILE_NORMAL)
            current_profile = PROFILE_NORMAL
            system_mode.promote(system_mode.SystemMode.NOMINAL, f"thermal_resume_{temp:.0f}C")
            emit(
                domain=Domain.SYSTEM,
                severity=Severity.INFO,
                detection_signal=f"cpu_temp={temp:.1f}C",
                recovery_action=RecoveryAction.NONE,
                outcome=f"resumed normal FPS (thermal cool {temp:.1f}°C)",
            )

        _stop_event.wait(POLL_INTERVAL)


def start_thermal_monitor() -> None:
    """Start the thermal de-rate monitor in a background thread."""
    if not THERMAL_ZONE.exists():
        logger.warning("Thermal zone %s not found — thermal monitor disabled", THERMAL_ZONE)
        return
    thread = threading.Thread(target=_thermal_loop, daemon=True, name="thermal-monitor")
    thread.start()
    logger.info(
        "Thermal monitor started (warn=%s°C, crit=%s°C, resume=%s°C, poll=%ds)",
        WARN_C, CRIT_C, RESUME_C, POLL_INTERVAL,
    )


def stop_thermal_monitor() -> None:
    """Signal the thermal monitor thread to stop (called on app shutdown)."""
    _stop_event.set()
