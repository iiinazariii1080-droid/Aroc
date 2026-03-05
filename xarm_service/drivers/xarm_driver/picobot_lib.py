#!/usr/bin/env python3
"""
gripper_controller.py

Библиотека для управления вакуумным гриппером piCOBOTe через Modbus‑интерфейс
(tgpio_modbus) xArm SDK.  Поддерживает:
  • VAC ON / VAC OFF
  • Чтение PDI (Process Data In) — вакуум, Part Present, Part Secured и др.
  • Чтение моментов суставов (get_joints_torque) для косвенной верификации
  • Парсинг Modbus‑ответов
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from typing import List, Optional

from models.grasp_types import GripperFeedback, GripperStatus

logger = logging.getLogger(__name__)

# ── Expected success response (kept for backward compat) ──────────────────
_EXPECTED_OK = [1, 8, 0, 1, 0, 0, 41, 1, 0, 0, 220]

# ── Modbus commands for piCOBOTe ──────────────────────────────────────────
CMD_VAC_ON  = [0x01, 0x07, 0x00, 0x01, 0x00, 0x29, 0x02, 0x01, 0x00, 0x82]
CMD_VAC_OFF = [0x01, 0x07, 0x00, 0x01, 0x00, 0x29, 0x02, 0x00, 0x00, 0x97]
# PDI read (index 40) with CRC8-CCITT over bytes [2..5]
# Spec example: 01-04-00-00-00-28-d8
CMD_READ_PDI = [0x01, 0x04, 0x00, 0x00, 0x00, 0x28, 0xD8]

# Special error response prefixes from piCOBOT RS485 protocol
_ERR_UNKNOWN_CMD = (0x01, 0x01, 0xFF)
_ERR_BUFFER_OVERRUN = (0x01, 0x01, 0xFE)


@dataclass
class PDISnapshot:
    """Parsed piCOBOTe Process Data In (index 40) snapshot.

    PDI structure (5 data bytes after Modbus header):
      byte0 = vacuum pressure (kPa, signed int8)
      byte1 bit0 = Part Present (PP setpoint achieved)
      byte1 bit1 = Part Secured (PS setpoint achieved)
      byte1 bit2 = Energy Saving reached
      byte1 bit3 = Atmospheric pressure reached
      byte1 bit4 = Automated Function Complete (AFC)
      byte1 bit5 = Membrane service warning
      byte1 bit6 = Motor stall detected
      byte2-3    = Hours to membrane service (uint16, big-endian)
      byte4      = PCB temperature (°C)
    """
    vacuum_kpa: int = 0
    part_present: bool = False
    part_secured: bool = False
    energy_saving: bool = False
    atm_reached: bool = False
    afc_complete: bool = False
    membrane_warn: bool = False
    motor_stall: bool = False
    membrane_hours: int = 0
    pcb_temperature: int = 0
    raw: List[int] = field(default_factory=list)

    @property
    def feedback(self) -> GripperFeedback:
        """Derive GripperFeedback from PDI flags."""
        if self.motor_stall:
            return GripperFeedback.ERROR
        if self.part_secured:
            return GripperFeedback.PART_GRIPPED
        if self.part_present:
            return GripperFeedback.VACUUM_NO_PART
        return GripperFeedback.OFF


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


class PDIUnsupportedError(GripperError):
    """PDI reading is unsupported by current end-effector communication mode."""
    pass


class GripperController:
    """
    Управление вакуумным гриппером piCOBOTe через Modbus RTU (RS‑485).

    Предоставляет:
      • activate() / deactivate()  — VAC ON / VAC OFF
      • read_pdi()                 — прямое чтение PDI (index 40)
      • get_status()               — агрегированное состояние через PDI
      • read_joints_torque()       — моменты суставов для contact detection
    """

    def __init__(self, xarm_api, baudrate: int = 115200, timeout: int = 100):
        self.api = xarm_api
        self._last_response: Optional[ModbusResponse] = None
        self._last_pdi: Optional[PDISnapshot] = None
        self._active: bool = False
        self._pdi_fail_streak: int = 0
        self._pdi_fail_streak_limit: int = 5
        self._pdi_supported: bool = True
        self._pdi_disabled_reason: Optional[str] = None
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

    @staticmethod
    def _crc8_ccitt(data: List[int], poly: int = 0x07, init: int = 0x00) -> int:
        """CRC-8-CCITT used by piCOBOT RS485 protocol."""
        crc = init
        for byte in data:
            crc ^= byte & 0xFF
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ poly) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
        return crc

    def _validate_picobot_crc(self, raw: List[int]) -> bool:
        """Validate CRC for piCOBOT response: covers byte[2]..byte[n-1], excludes crc byte."""
        if len(raw) < 4:
            return False
        expected = self._crc8_ccitt(raw[2:-1])
        return expected == (raw[-1] & 0xFF)

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
        """Activate vacuum (VAC ON). Returns raw response list."""
        logger.info("Activating gripper (VAC ON)…")
        parsed = self._send_modbus_command(CMD_VAC_ON)
        if parsed.success:
            self._active = True
        logger.info("Gripper activate → ok=%s  raw=%s", parsed.success, parsed.raw)
        return parsed.raw

    def deactivate(self) -> list:
        """Deactivate vacuum (VAC OFF). Returns raw response list."""
        logger.info("Deactivating gripper (VAC OFF)…")
        parsed = self._send_modbus_command(CMD_VAC_OFF)
        if parsed.success:
            self._active = False
        logger.info("Gripper deactivate → ok=%s  raw=%s", parsed.success, parsed.raw)
        return parsed.raw

    def set_custom_command(self, command: list, min_response_length: int = 10) -> list:
        """Send an arbitrary Modbus command. Returns raw response list."""
        logger.info("Custom Modbus TX: %s", command)
        parsed = self._send_modbus_command(command, min_response_length)
        return parsed.raw

    # ── PDI: Process Data In (index 40) ───────────────────────────────────────

    def read_pdi(self) -> PDISnapshot:
        """Read PDI from piCOBOTe via Modbus RTU.

        PDI provides: vacuum_kpa, part_present, part_secured, energy_saving,
        atm_reached, afc_complete, membrane_warn, motor_stall,
        membrane_hours, pcb_temperature.

        Raises GripperError on communication failure.
        """
        ret, response = self.api.getset_tgpio_modbus_data(
            CMD_READ_PDI,
            min_res_len=0,
            host_id=9,
            is_transparent_transmission=True,
            use_503_port=False,
        )
        raw = list(response) if response else []
        if not raw:
            raise PDIUnsupportedError("PDI unsupported response frame: raw=[]")
        if tuple(raw[:3]) == _ERR_UNKNOWN_CMD:
            raise PDIUnsupportedError(f"piCOBOT unknown command response: raw={raw}")
        if tuple(raw[:3]) == _ERR_BUFFER_OVERRUN:
            raise PDIUnsupportedError(
                f"piCOBOT buffer overrun / invalid CRC response: raw={raw}"
            )
        if ret != 0 and not raw:
            raise GripperError(f"Modbus command failed, code: {ret}, response: {raw}")
        if len(raw) < 11:
            raise GripperError(f"PDI response too short: len={len(raw)}, raw={raw}")
        if not self._validate_picobot_crc(raw):
            raise GripperError(f"PDI CRC mismatch: raw={raw}")

        parsed = self._parse_response(raw)
        self._last_response = parsed
        # Successful read response (piCOBOT spec):
        # 0:protocol, 1:payload_len, 2:cmdHB, 3:cmdLB, 4:error_bit,
        # 5:indexHB, 6:indexLB, 7:data_len, 8:lock_bit, 9..:data, last:crc
        error_bit = raw[4]
        index = (raw[5] << 8) | raw[6]
        data_len = raw[7]

        if error_bit != 0:
            status_code = raw[9] if len(raw) > 9 else None
            raise GripperError(
                f"PDI read operation failed: index={index}, status={status_code}, raw={raw}"
            )
        if index != 0x0028:
            raise GripperError(f"Unexpected index in PDI response: index={index}, raw={raw}")
        if data_len < 5:
            raise GripperError(
                f"PDI data length too short: expected >=5, got {data_len}, raw={raw}"
            )

        payload = raw[9:9 + data_len]
        if len(payload) < 5:
            raise GripperError(f"PDI payload truncated: len={len(payload)}, raw={raw}")

        # Byte 0: vacuum pressure, signed int8
        vacuum_kpa = struct.unpack('b', bytes([payload[0]]))[0]

        # Byte 1: status bits
        status_byte = payload[1]
        part_present  = bool(status_byte & 0x01)
        part_secured  = bool(status_byte & 0x02)
        energy_saving = bool(status_byte & 0x04)
        atm_reached   = bool(status_byte & 0x08)
        afc_complete  = bool(status_byte & 0x10)
        membrane_warn = bool(status_byte & 0x20)
        motor_stall   = bool(status_byte & 0x40)

        # Bytes 2-3: hours to membrane service (uint16, big-endian)
        membrane_hours = (payload[2] << 8) | payload[3]

        # Byte 4: PCB temperature
        pcb_temperature = payload[4]

        pdi = PDISnapshot(
            vacuum_kpa=vacuum_kpa,
            part_present=part_present,
            part_secured=part_secured,
            energy_saving=energy_saving,
            atm_reached=atm_reached,
            afc_complete=afc_complete,
            membrane_warn=membrane_warn,
            motor_stall=motor_stall,
            membrane_hours=membrane_hours,
            pcb_temperature=pcb_temperature,
            raw=parsed.raw,
        )

        self._last_pdi = pdi
        self._pdi_fail_streak = 0

        logger.debug(
            "PDI: vac=%dkPa PP=%s PS=%s ES=%s stall=%s temp=%d°C",
            vacuum_kpa, part_present, part_secured, energy_saving,
            motor_stall, pcb_temperature,
        )
        return pdi

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
        """Return a high‑level GripperStatus snapshot via PDI."""
        pdi: Optional[PDISnapshot] = None
        if self._pdi_supported:
            try:
                pdi = self.read_pdi()
            except PDIUnsupportedError as exc:
                self._pdi_supported = False
                self._pdi_disabled_reason = f"unsupported_pdi_protocol: {exc}"
                logger.error("Disabling PDI reads immediately: %s", exc)
            except Exception as exc:
                self._pdi_fail_streak += 1
                logger.warning(
                    "PDI read failed (%d/%d): %s",
                    self._pdi_fail_streak, self._pdi_fail_streak_limit, exc,
                )
                if self._pdi_fail_streak >= self._pdi_fail_streak_limit:
                    self._pdi_supported = False
                    self._pdi_disabled_reason = f"persistent_pdi_failures: {exc}"
                    logger.error(
                        "Disabling PDI reads after %d consecutive failures",
                        self._pdi_fail_streak,
                    )

        if pdi:
            return GripperStatus(
                active=self._active,
                feedback=pdi.feedback,
                vacuum_level=float(pdi.vacuum_kpa),
                part_present=pdi.part_present,
                part_secured=pdi.part_secured,
                energy_saving=pdi.energy_saving,
                motor_stall=pdi.motor_stall,
                pcb_temperature=pdi.pcb_temperature,
                membrane_hours=pdi.membrane_hours,
                membrane_warn=pdi.membrane_warn,
                modbus_raw=pdi.raw,
                sensor_supported=True,
            )

        return GripperStatus(
            active=self._active,
            feedback=GripperFeedback.UNKNOWN,
            modbus_raw=self._last_response.raw if self._last_response else None,
            sensor_supported=False,
        )

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def pdi_supported(self) -> bool:
        return self._pdi_supported

    @property
    def pdi_disabled_reason(self) -> Optional[str]:
        return self._pdi_disabled_reason

