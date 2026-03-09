"""
Конфигурация приложения.
"""
import os
from typing import Optional, Dict, Any
from pydantic import Field
from pydantic_settings import BaseSettings
from shared_config.network import DEVICES


class Settings(BaseSettings):
    """Настройки приложения."""
    
    # Symovo AGV настройки
    symovo_car_ip: str = Field(default=DEVICES.SYMOVO_CAR_IP)
    symovo_robot_number: int = Field(default=15)
    symovo_timeout_seconds: int = Field(default=10)
    symovo_operation_timeout_seconds: Optional[float] = Field(default=30.0)
    symovo_motion_timeout_seconds: Optional[float] = Field(default=None)
    symovo_allow_invalid_certs: bool = Field(default=True)
    # If true, backend will try to call Symovo "drive_mode" endpoint when drive_ready is false
    # right before starting a transport. Disabled by default for safety.
    symovo_auto_set_drive_mode: bool = Field(default=False)
    symovo_auto_set_drive_mode_wait_s: float = Field(default=1.0)
    # If true, backend will clear ALL transports on the controller at startup (destructive).
    symovo_clear_transports_on_startup: bool = Field(default=False)
    # If true, backend will clear ALL transports on the controller right before starting *each* new navigateTo
    # (destructive). This keeps Symovo "history" clean and avoids stale transports affecting UI/recovery.
    symovo_clear_transports_before_navigate: bool = Field(default=False)

    # When laser_timeout or waiting_for_scanner is active at navigation start,
    # wait up to this many seconds for the flag to clear before failing.
    # New commands arriving during the wait replace the pending one (no queue).
    laser_timeout_wait_s: float = Field(default=30.0)
    # Polling interval while waiting for scanner flags to clear.
    laser_timeout_poll_interval_s: float = Field(default=2.0)

    # When an HTTP command arrives but MQTT is temporarily disconnected,
    # wait up to this many seconds for the background reconnect to succeed
    # before returning 503.  Set to 0 to fail immediately (old behaviour).
    mqtt_command_retry_wait_s: float = Field(default=15.0)
    mqtt_command_retry_poll_s: float = Field(default=0.5)

    # If true, backend will consider navigation "arrived" when robot is near goal for a while,
    # even if Symovo transport never transitions to FINISHED (prevents infinite 99%).
    symovo_force_arrival_on_proximity: bool = Field(default=True)
    symovo_arrival_dist_m: float = Field(default=0.25)
    symovo_arrival_dwell_s: float = Field(default=2.5)

    # Charger workflow
    # If navigateTo target_id matches charger_target_name, backend will activate (enable) a charging station
    # with name charger_station_name AFTER navigation arrives (to trigger Symovo internal docking script).
    charger_activation_enabled: bool = Field(default=True)
    charger_target_name: str = Field(default="CHARGER")
    charger_station_name: str = Field(default="charger")
    
    # HTTP настройки
    service_host: str = Field(default="0.0.0.0")
    service_port: int = Field(default=7905)
    uvicorn_workers: int = Field(default=1)
    
    # Логирование
    log_level: str = Field(default="INFO")
    
    # Кеширование
    cache_ttl_seconds: int = Field(default=300)  # 5 минут
    # Background cache: max age (seconds) before route falls back to direct request
    cache_max_age_s: float = Field(default=10.0)
    # Background status polling rate (Hz). 0.5 = once per 2 seconds.
    status_cache_hz: float = Field(default=0.5, gt=0.0, le=10.0)
    
    # MQTT настройки (AE.HUB)
    mqtt_broker_host: Optional[str] = Field(default=None)
    mqtt_broker_port: Optional[int] = Field(default=None)
    mqtt_username: Optional[str] = Field(default=None)
    mqtt_password: Optional[str] = Field(default=None)
    mqtt_use_tls: bool = Field(default=False)
    mqtt_tls_insecure: bool = Field(default=False)
    mqtt_ca_cert: Optional[str] = Field(default=None)
    mqtt_client_id: Optional[str] = Field(default=None)
    
    # AE.HUB настройки
    robot_id: str = Field(default="fahrdummy-01")
    
    # Publish rates
    navigation_status_hz: float = Field(default=1.0, gt=0.0, le=100.0)  # 1 Hz
    position_status_hz: float = Field(default=2.0, gt=0.0, le=100.0)  # 2 Hz
    # How long to hold terminal ARRIVED status before switching to IDLE (prevents arrived->idle flicker).
    navigation_terminal_hold_s: float = Field(default=3.0, ge=0.0, le=60.0)
    
    # Timeouts
    transport_watch_timeout: float = Field(default=30.0)  # seconds
    readiness_check_interval: float = Field(default=1.0)  # seconds

    # Persistence (SRS recovery)
    persistence_enabled: bool = Field(default=True)
    persistence_path: str = Field(default="data/state.json")
    
    # Startup behavior
    # If true, state recovery will be awaited during startup. If false (default), recovery runs in background,
    # reducing startup time substantially when controller is slow/unreachable.
    startup_blocking_recovery: bool = Field(default=False)
    # If true, destructive controller cleanup (clear transports) will be awaited during startup. If false (default),
    # cleanup runs in background to avoid delaying readiness.
    startup_blocking_clear_transports: bool = Field(default=False)

    # HTTP commands delivery
    # If true, HTTP /commands endpoints may execute commands locally (without MQTT) in dev/test mode.
    allow_direct_http_commands: bool = Field(default=False)

    # Teleop (joystick/keyboard): отдельный HTTP‑сервер в потоке для управления по speed/angular_speed.
    teleop_enabled: bool = Field(default=True)
    teleop_host: str = Field(default="127.0.0.1")
    teleop_port: int = Field(default=7906)
    teleop_default_linear_speed: float = Field(default=0.1)
    teleop_default_angular_speed: float = Field(default=0.5)
    teleop_default_duration: float = Field(default=0.25)

    # Event topics (ack/state/result)
    mqtt_events_prefix: str = Field(default="aroc/robot/{robot_id}/events")
    
    model_config = {
        "env_file": ".env",
        "case_sensitive": False,
        "env_file_encoding": "utf-8",
    }

    @property
    def symovo_base_url(self) -> str:
        """Базовый URL для Symovo API."""
        return f"https://{self.symovo_car_ip}/v0"
    
    def mqtt_events_topic(self, kind: str) -> str:
        """Resolve events topic using current robot_id."""
        base = self.mqtt_events_prefix.replace("{robot_id}", self.robot_id)
        return f"{base}/{kind}"


class TeleopConfig:
    """Thread-safe container for mutable teleop parameters.

    Reads/writes are protected by a threading.Lock so they can be safely
    accessed from both the main event-loop thread and the teleop server thread.
    """

    def __init__(self, settings: Settings) -> None:
        import threading
        self._lock = threading.Lock()
        self._duration = float(settings.teleop_default_duration)
        self._linear_speed = float(settings.teleop_default_linear_speed)
        self._angular_speed = float(settings.teleop_default_angular_speed)

    # -- atomic getters ------------------------------------------------
    @property
    def duration(self) -> float:
        with self._lock:
            return self._duration

    @property
    def linear_speed(self) -> float:
        with self._lock:
            return self._linear_speed

    @property
    def angular_speed(self) -> float:
        with self._lock:
            return self._angular_speed

    # -- atomic setters ------------------------------------------------
    @duration.setter
    def duration(self, v: float) -> None:
        with self._lock:
            self._duration = float(v)

    @linear_speed.setter
    def linear_speed(self, v: float) -> None:
        with self._lock:
            self._linear_speed = float(v)

    @angular_speed.setter
    def angular_speed(self, v: float) -> None:
        with self._lock:
            self._angular_speed = float(v)

    def snapshot(self) -> dict:
        """Return current values as a dict (single lock acquisition)."""
        with self._lock:
            return {
                "duration": self._duration,
                "linear_m_s": self._linear_speed,
                "angular_rad_s": self._angular_speed,
            }

# Глобальный экземпляр настроек
settings = Settings()

# Thread-safe mutable teleop parameters (shared between main loop and teleop thread)
teleop_config = TeleopConfig(settings)


def log_config_summary() -> None:
    """Log loaded config values. Call after logging.basicConfig()."""
    import logging
    _logger = logging.getLogger(__name__)
    _logger.info("Config loaded - position_status_hz: %s Hz, navigation_status_hz: %s Hz",
                 settings.position_status_hz, settings.navigation_status_hz)