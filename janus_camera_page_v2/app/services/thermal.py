"""CPU thermal monitor with automatic FPS de-rating.

Reads ``/sys/class/thermal/thermal_zone0/temp`` (BCM2835 on Pi 5) and
triggers system mode transitions when the SoC crosses temperature thresholds.

Thresholds (configurable via env — see Settings for all names):
    THERMAL_WARN_C   = 70  → degrade system mode (fps_profile written by system_mode)
    THERMAL_CRIT_C   = 80  → transition to SAFE mode (pipeline stopped)
    THERMAL_RESUME_C = 65  → promote back to NOMINAL (hysteresis)

fps_profile ownership: the thermal monitor only changes the *system mode*.
The actual fps_profile file is written exclusively by
``system_mode._post_transition()`` → ``_apply_fps_profile()``, ensuring a
single source of truth between mode state and pipeline configuration.

The monitor runs in a daemon thread started by ``start_thermal_monitor()``.
"""
from __future__ import annotations

import logging
import threading

from app.core.settings import get_settings
from app.services.fdir_events import Domain, Severity, emit, RecoveryAction
from app.services import system_mode

logger = logging.getLogger(__name__)

# Stop event — set by stop_thermal_monitor() during lifespan shutdown so the
# daemon thread exits before shutdown_listener_executor() is called.
_thermal_stop = threading.Event()
_thermal_thread: threading.Thread | None = None

PROFILE_NORMAL = "normal"     # e.g. 15 FPS
PROFILE_LOW = "low"           # e.g. 5 FPS
PROFILE_STOP = "stop"         # pipeline should halt


def read_cpu_temp() -> float | None:
    """Read CPU temperature in °C. Returns None if unavailable."""
    thermal_zone = get_settings().thermal_zone_path
    try:
        raw = thermal_zone.read_text().strip()
        return int(raw) / 1000.0
    except PermissionError:
        logger.error("Permission denied reading %s — thermal monitoring degraded", thermal_zone)
        return None
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Unexpected error reading CPU temperature: %s", exc)
        return None


def set_fps_profile(profile: str) -> None:
    """Write the desired FPS profile to a well-known path atomically.

    Uses atomic_write_text (tempfile + fsync + rename) so the pipeline
    always reads a complete value even on embedded systems with slow flash.
    """
    try:
        from app.utils.fs import atomic_write_text
        atomic_write_text(get_settings().fps_profile_path, profile + "\n")
        logger.info("FPS profile set to: %s", profile)
    except Exception as exc:
        logger.warning("Could not write FPS profile: %s", exc)


def _thermal_loop() -> None:
    """Main thermal monitoring loop (runs in daemon thread).

    Top-level try/except ensures the thread never dies silently.
    A heartbeat gauge (``camstack_thermal_monitor_heartbeat``) is updated
    every iteration so Prometheus can alert on staleness if the thread
    unexpectedly stops.
    """
    import time as _time
    from app.metrics.safe import safe_set

    while not _thermal_stop.is_set():
        try:
            safe_set("thermal_monitor_heartbeat", _time.time())

            settings = get_settings()
            warn_c = settings.thermal_warn_c
            crit_c = settings.thermal_crit_c
            resume_c = settings.thermal_resume_c
            poll_interval = settings.thermal_poll_sec

            temp = read_cpu_temp()
            if temp is None:
                _thermal_stop.wait(timeout=poll_interval)
                continue

            safe_set("cpu_temp_celsius", temp)

            # Read the canonical fps_profile from system_mode — never maintain
            # a shadow copy.  This ensures the thermal monitor always observes
            # mode changes made by the watchdog or FDIR ladder.
            active_profile = system_mode.current_policy().fps_profile

            if temp >= crit_c and active_profile != PROFILE_STOP:
                logger.critical("CPU temp %.1f°C ≥ %s°C → STOPPING pipeline", temp, crit_c)
                system_mode.transition(system_mode.SystemMode.SAFE, f"thermal_critical_{temp:.0f}C")
                emit(
                    domain=Domain.SYSTEM,
                    severity=Severity.CRITICAL,
                    detection_signal=f"cpu_temp={temp:.1f}C",
                    recovery_action=RecoveryAction.DEGRADE_PROFILE,
                    outcome=f"pipeline stopped (thermal critical {temp:.1f}°C)",
                )

            elif temp >= warn_c and active_profile == PROFILE_NORMAL:
                logger.warning("CPU temp %.1f°C ≥ %s°C → LOW FPS profile", temp, warn_c)
                system_mode.degrade(f"thermal_warn_{temp:.0f}C")
                emit(
                    domain=Domain.SYSTEM,
                    severity=Severity.WARN,
                    detection_signal=f"cpu_temp={temp:.1f}C",
                    recovery_action=RecoveryAction.DEGRADE_PROFILE,
                    outcome=f"switched to low FPS profile (thermal warn {temp:.1f}°C)",
                )

            elif temp <= resume_c and active_profile != PROFILE_NORMAL:
                logger.info("CPU temp %.1f°C ≤ %s°C → resuming NORMAL profile", temp, resume_c)
                system_mode.promote(system_mode.SystemMode.NOMINAL, f"thermal_resume_{temp:.0f}C")
                emit(
                    domain=Domain.SYSTEM,
                    severity=Severity.INFO,
                    detection_signal=f"cpu_temp={temp:.1f}C",
                    recovery_action=RecoveryAction.NONE,
                    outcome=f"resumed normal FPS (thermal cool {temp:.1f}°C)",
                )

        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception:
            logger.exception(
                "Unexpected error in thermal monitor — loop continues but "
                "this iteration's readings may be stale"
            )
            emit(
                domain=Domain.SYSTEM,
                severity=Severity.ERROR,
                detection_signal="thermal_monitor_exception",
                recovery_action=RecoveryAction.NONE,
                outcome="thermal monitor loop caught unexpected exception",
            )

        _thermal_stop.wait(timeout=poll_interval)


def start_thermal_monitor() -> None:
    """Start the thermal de-rate monitor in a background thread."""
    global _thermal_thread
    settings = get_settings()
    thermal_zone = settings.thermal_zone_path
    warn_c = settings.thermal_warn_c
    crit_c = settings.thermal_crit_c
    resume_c = settings.thermal_resume_c

    if not thermal_zone.exists():
        logger.warning("Thermal zone %s not found — thermal monitor disabled", thermal_zone)
        return

    if not (resume_c < warn_c < crit_c):
        logger.error(
            "THERMAL THRESHOLD MISCONFIGURATION: require THERMAL_RESUME_C(%.1f) < "
            "THERMAL_WARN_C(%.1f) < THERMAL_CRIT_C(%.1f). Monitor may oscillate or "
            "skip warning stage.",
            resume_c, warn_c, crit_c,
        )
    _thermal_stop.clear()
    _thermal_thread = threading.Thread(target=_thermal_loop, daemon=True, name="thermal-monitor")
    _thermal_thread.start()
    logger.info(
        "Thermal monitor started (warn=%s°C, crit=%s°C, resume=%s°C, poll=%ds)",
        warn_c, crit_c, resume_c, settings.thermal_poll_sec,
    )


def stop_thermal_monitor() -> None:
    """Signal the thermal monitor thread to stop and wait for it to exit.

    Waits up to 5 s for the thread to finish so that subsequent calls to
    ``shutdown_listener_executor()`` don't race with a still-running thermal
    loop that might submit work to the executor.
    """
    global _thermal_thread
    _thermal_stop.set()
    if _thermal_thread is not None:
        _thermal_thread.join(timeout=5)
        if _thermal_thread.is_alive():
            logger.warning("Thermal monitor thread did not exit within 5 s")
        _thermal_thread = None
