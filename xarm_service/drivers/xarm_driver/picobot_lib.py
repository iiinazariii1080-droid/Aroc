#!/usr/bin/env python3
"""
gripper_controller.py

Библиотека для управления вакуумным гриппером piCOBOTe через Modbus‑интерфейс
(tgpio_modbus) xArm SDK.  Поддерживает:
  • VAC ON / VAC OFF
  • Чтение статуса захвата через xArm SDK (get_vacuum_gripper)
  • Чтение моментов суставов (get_joints_torque) для косвенной верификации
  • Парсинг Modbus‑ответов вместо magic‑bytes сравнения
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from models.grasp_types import GripperFeedback, GripperStatus

logger = logging.getLogger(__name__)

# ── Expected success response (kept for backward compat) ──────────────────
_EXPECTED_OK = [1, 8, 0, 1, 0, 0, 41, 1, 0, 0, 220]


@dataclass
class ModbusResponse:
    """Parsed Modbus RTU response."""
    raw: List[int]
    slave_addr: int = 0
    function_code: int = 0
    data: List[int] = None  # type: ignore[assignment]
    success: bool = False

    def __post_init__(self):
        if self.data is None:
            self.data = []


class GripperError(Exception):
    """Исключение, выбрасываемое при ошибках управления гриппером."""
    pass


class GripperController:
    """
    Управление вакуумным гриппером piCOBOTe через Modbus RTU (RS‑485).

    Дополнительно предоставляет:
      • ``get_status()``  — агрегированное состояние (вакуум + SDK)
      • ``read_vacuum_via_sdk()`` — xArm ``get_vacuum_gripper`` (−1/0/1)
      • ``read_joints_torque()`` — моменты суставов для contact detection
    """

    def __init__(self, xarm_api, baudrate: int = 115200, timeout: int = 100):
        self.api = xarm_api
        self._last_response: Optional[ModbusResponse] = None
        self._active: bool = False
        self._sdk_vacuum_supported: bool = True
        self._sdk_vacuum_disabled_reason: Optional[str] = None
        self._sdk_vacuum_fail_streak: int = 0
        self._sdk_vacuum_fail_streak_limit: int = 2
        self._configure_modbus(baudrate, timeout)

    def _configure_modbus(self, baudrate: int, timeout: int):
        """Настройка параметров Modbus‑соединения для tool GPIO."""
        ret = self.api.set_tgpio_modbus_baudrate(baudrate)
        if ret != 0:
            logger.error("Не удалось установить модбас-скорость: %s", baudrate)
            raise GripperError(f"Ошибка установки модбас-скорости, код: {ret}")
        ret = self.api.set_tgpio_modbus_timeout(timeout)
        if ret != 0:
            logger.error("Не удалось установить модбас-таймаут: %s", timeout)
            raise GripperError(f"Ошибка установки модбас-таймаута, код: {ret}")
        logger.info("Modbus configured: baudrate=%s, timeout=%s", baudrate, timeout)

    # ── Low‑level transport ──────────────────────────────────────────────────

    @staticmethod
    def _parse_response(raw: list) -> ModbusResponse:
        """Parse a raw Modbus RTU response list into a structured object."""
        resp = ModbusResponse(raw=list(raw))
        if raw and len(raw) >= 2:
            resp.slave_addr = raw[0]
            resp.function_code = raw[1]
            resp.data = raw[2:]
        # Success = matches known OK pattern OR function_code matches request (no error bit)
        resp.success = raw == _EXPECTED_OK or (len(raw) >= 2 and (raw[1] & 0x80) == 0)
        return resp

    def _send_modbus_command(
        self,
        command: list,
        min_response_length: int = 10,
        host_id: int = 9,
        is_transparent: bool = True,
        use_503_port: bool = False,
    ) -> ModbusResponse:
        """Send a raw Modbus command and return a parsed response."""
        logger.debug("Modbus TX: %s", command)
        ret, response = self.api.getset_tgpio_modbus_data(
            command,
            min_res_len=min_response_length,
            host_id=host_id,
            is_transparent_transmission=is_transparent,
            use_503_port=use_503_port,
        )
        if ret != 0:
            logger.error("Modbus error code: %s", ret)
            raise GripperError(f"Modbus command failed, code: {ret}")
        parsed = self._parse_response(list(response) if response else [])
        self._last_response = parsed
        logger.debug("Modbus RX: %s  (ok=%s)", parsed.raw, parsed.success)
        return parsed

    # ── Vacuum ON / OFF ───────────────────────────────────────────────────────

    def activate(self) -> list:
        """Activate vacuum (VAC ON). Returns raw response list for backward compat."""
        cmd_vac_on = [0x01, 0x07, 0x00, 0x01, 0x00, 0x29, 0x02, 0x01, 0x00, 0x82]
        logger.info("Activating gripper (VAC ON)…")
        parsed = self._send_modbus_command(cmd_vac_on)
        if parsed.success:
            self._active = True
        logger.info("Gripper activate → ok=%s  raw=%s", parsed.success, parsed.raw)
        return parsed.raw

    def deactivate(self) -> list:
        """Deactivate vacuum (VAC OFF). Returns raw response list for backward compat."""
        cmd_vac_off = [0x01, 0x07, 0x00, 0x01, 0x00, 0x29, 0x02, 0x00, 0x00, 0x97]
        logger.info("Deactivating gripper (VAC OFF)…")
        parsed = self._send_modbus_command(cmd_vac_off)
        if parsed.success:
            self._active = False
        logger.info("Gripper deactivate → ok=%s  raw=%s", parsed.success, parsed.raw)
        return parsed.raw

    def set_custom_command(self, command: list, min_response_length: int = 10) -> list:
        """Send an arbitrary Modbus command. Returns raw response list."""
        logger.info("Custom Modbus TX: %s", command)
        parsed = self._send_modbus_command(command, min_response_length)
        return parsed.raw

    # ── Vacuum status via xArm SDK ────────────────────────────────────────────

    def read_vacuum_via_sdk(self) -> int:
        """
        Use the xArm built‑in ``get_vacuum_gripper()`` call.

        Returns
        -------
        int
            −1 = vacuum off, 0 = vacuum on but no object, 1 = object picked.
            Returns −99 on communication error.
        """
        if not self._sdk_vacuum_supported:
            return -99
        try:
            code, state = self.api.get_vacuum_gripper()
            if code != 0:
                logger.warning("get_vacuum_gripper code=%s", code)
                self._sdk_vacuum_fail_streak += 1
                if int(code) == 19:
                    self._sdk_vacuum_supported = False
                    self._sdk_vacuum_disabled_reason = "controller_error_code_19"
                    logger.warning("Disabling SDK vacuum status reads due to code=19 (degraded mode)")
                elif self._sdk_vacuum_fail_streak >= self._sdk_vacuum_fail_streak_limit:
                    self._sdk_vacuum_supported = False
                    self._sdk_vacuum_disabled_reason = f"persistent_nonzero_code_{int(code)}"
                    logger.warning(
                        "Disabling SDK vacuum status reads after %d consecutive non-zero results",
                        self._sdk_vacuum_fail_streak,
                    )
                return -99
            self._sdk_vacuum_fail_streak = 0
            return int(state)
        except Exception as exc:
            logger.warning("get_vacuum_gripper exception: %s", exc)
            self._sdk_vacuum_fail_streak += 1
            msg = str(exc).lower()
            if "controllererror" in msg or "code: 19" in msg or "end module communication error" in msg:
                self._sdk_vacuum_supported = False
                self._sdk_vacuum_disabled_reason = str(exc)
                logger.warning("Disabling SDK vacuum status reads after C19/end-module communication error")
            elif self._sdk_vacuum_fail_streak >= self._sdk_vacuum_fail_streak_limit:
                self._sdk_vacuum_supported = False
                self._sdk_vacuum_disabled_reason = "persistent_sdk_vacuum_exceptions"
                logger.warning(
                    "Disabling SDK vacuum status reads after %d consecutive exceptions",
                    self._sdk_vacuum_fail_streak,
                )
            return -99

    # ── Joint torques (for contact detection) ─────────────────────────────────

    def read_joints_torque(self) -> Optional[List[float]]:
        """Read current joint torques (Nm). Returns None on error."""
        try:
            code, torques = self.api.get_joints_torque()
            if code != 0:
                logger.debug("get_joints_torque code=%s", code)
                return None
            return list(torques)
        except Exception as exc:
            logger.debug("get_joints_torque exception: %s", exc)
            return None

    # ── Aggregated status ─────────────────────────────────────────────────────

    def get_status(self) -> GripperStatus:
        """Return a high‑level ``GripperStatus`` snapshot."""
        sdk_state = self.read_vacuum_via_sdk()
        if sdk_state == 1:
            fb = GripperFeedback.PART_GRIPPED
        elif sdk_state == 0:
            fb = GripperFeedback.VACUUM_NO_PART
        elif sdk_state == -1:
            fb = GripperFeedback.OFF
        else:
            fb = GripperFeedback.UNKNOWN

        return GripperStatus(
            active=self._active,
            feedback=fb,
            modbus_raw=self._last_response.raw if self._last_response else None,
        )

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def sdk_vacuum_supported(self) -> bool:
        return self._sdk_vacuum_supported

    @property
    def sdk_vacuum_disabled_reason(self) -> Optional[str]:
        return self._sdk_vacuum_disabled_reason

