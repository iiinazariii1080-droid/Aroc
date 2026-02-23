"""
Charger workflow logic — consolidated charger activation/deactivation rules.

Single source of truth for charger station management (НАР-1).
Previously split between CommandHandler (deactivate on cancel) and
StatusPublisher (activate after arrival).
"""
import logging
from typing import Any, Optional

from app.config import settings
from domain.models import NavigationSession

_LOGGER = logging.getLogger(__name__)


async def maybe_deactivate_on_cancel(
    symovo_client: Any,
    active_transport: Any,
) -> None:
    """Deactivate charger station when a charger-bound navigation is cancelled.

    Without this, the charger station remains enabled after cancel, which can
    cause the robot to dock unexpectedly on the next approach to that area.

    Args:
        symovo_client: SymovoAgvClient instance.
        active_transport: The transport being cancelled (needs .target_id).
    """
    if not settings.charger_activation_enabled:
        return
    target_id = getattr(active_transport, "target_id", None) or ""
    if not target_id or target_id.strip().upper() != settings.charger_target_name.strip().upper():
        return
    _LOGGER.info(
        "Cancel for charger target '%s' — deactivating charger station '%s'",
        target_id,
        settings.charger_station_name,
    )
    try:
        station_id = await symovo_client.set_charging_station_enabled_by_name(
            settings.charger_station_name,
            enabled=False,
        )
        if station_id is None:
            _LOGGER.warning(
                "Charger deactivation on cancel: station '%s' not found on controller",
                settings.charger_station_name,
            )
        else:
            _LOGGER.info(
                "Charger station deactivated on cancel: name=%s id=%s",
                settings.charger_station_name,
                station_id,
            )
    except Exception as e:
        _LOGGER.error(
            "Charger deactivation on cancel failed: station_name=%s error=%s",
            settings.charger_station_name,
            str(e),
            exc_info=True,
        )


async def maybe_activate_after_arrival(
    symovo_client: Any,
    session: NavigationSession,
) -> None:
    """Activate charger station after robot arrives near the charger pose.

    Triggers Symovo internal docking/parking script.

    Args:
        symovo_client: SymovoAgvClient instance.
        session: The navigation session that reached arrival state.
    """
    target = (session.target_id or "").strip()

    _LOGGER.debug(
        "Checking charger activation: enabled=%s, target=%s, expected_target=%s, station_name=%s",
        settings.charger_activation_enabled,
        target,
        settings.charger_target_name,
        settings.charger_station_name,
    )

    if not settings.charger_activation_enabled:
        _LOGGER.debug("Charger activation disabled in config (CHARGER_ACTIVATION_ENABLED=false)")
        return

    if not target:
        _LOGGER.debug("Charger activation skipped: target_id is empty")
        return

    if target.upper() != settings.charger_target_name.strip().upper():
        _LOGGER.debug(
            "Charger activation skipped: target '%s' does not match expected '%s'",
            target,
            settings.charger_target_name,
        )
        return

    _LOGGER.info(
        "Attempting to activate charger station: target=%s, station_name=%s",
        target,
        settings.charger_station_name,
    )

    try:
        station_id = await symovo_client.set_charging_station_enabled_by_name(
            settings.charger_station_name,
            enabled=True,
        )
        if station_id is None:
            _LOGGER.warning(
                "Charger activation requested (target=%s) but station not found: %s. "
                "Check CHARGER_STATION_NAME setting and verify station exists on controller.",
                target,
                settings.charger_station_name,
            )
        else:
            _LOGGER.info(
                "Charger station activated successfully: name=%s id=%s (target=%s)",
                settings.charger_station_name,
                station_id,
                target,
            )
    except Exception as e:
        _LOGGER.error(
            "Charger station activation failed: station_name=%s target=%s error=%s",
            settings.charger_station_name,
            target,
            str(e),
            exc_info=True,
        )


async def maybe_deactivate_on_drive_mode(symovo_client: Any) -> None:
    """Deactivate charger station when drive mode is enabled.

    Mirror of the "activate charger when navigating to CHARGER" flow:
    when motors are explicitly turned on, ensure the charger station is off
    so the robot doesn't re-dock unexpectedly.

    Args:
        symovo_client: SymovoAgvClient instance.
    """
    if not settings.charger_activation_enabled:
        return

    station_name = getattr(settings, "charger_station_name", "charger") or "charger"
    try:
        sid = await symovo_client.set_charging_station_enabled_by_name(station_name, enabled=False)
        if sid is not None:
            _LOGGER.info(
                "Drive mode ON: deactivated charging station name=%s id=%s",
                station_name,
                sid,
            )
        else:
            _LOGGER.debug(
                "Drive mode ON: no charging station named '%s' to deactivate",
                station_name,
            )
    except Exception as e:
        _LOGGER.warning(
            "Drive mode ON: failed to deactivate charging station name=%s: %s. Proceeding with drive_mode.",
            station_name,
            e,
        )


async def disable_all_before_move(symovo_client: Any) -> None:
    """Disable all charging stations before starting a transport move.

    Safety-critical: ensures the robot won't attempt to dock mid-navigation.
    Raises DeviceError if stations fail to deactivate.

    Args:
        symovo_client: SymovoAgvClient instance.
    """
    from exceptions import DeviceError

    await symovo_client.disable_all_charging_stations()
    ok = await symovo_client.wait_until_charging_stations_inactive()
    if not ok:
        raise DeviceError(
            "Не удалось деактивировать все зарядные станции, движение запрещено"
        )
