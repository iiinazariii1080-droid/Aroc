#!/usr/bin/env python3
"""
dryve D1 Modbus TCP Gateway emulator (FC=0x2B, MEI=0x0D) — "behavior 1:1" focus.

Goals (based on your logs + real-world DS402 expectations):
- Correct Modbus TCP MBAP framing and strict Length behavior.
- Implements dryve gateway telegram layout (FC=0x2B, MEI=0x0D).
- READ request length MUST be 0x000D (13 bytes after UnitId) and contains no data.
- READ response length MUST be 0x000D + byte_count, includes data.
- WRITE request length MUST be 0x000D + byte_count, includes data.
- WRITE response MUST be 0x000D and byte_count=0.
- DS402-ish behavior:
  - 0x6061 (Mode display) mirrors 0x6060 writes.
  - Motion is started by rising edge of Controlword bit4 ("new set-point").
  - Controlword decoding is by bits (not exact value) so 0x000B/0x002F/0x003F patterns work.
  - Statusword reflects state + target reached + homing attained.
- Keeps your HTTP endpoints (/version, /clients, /events, /emergency).

Run:
  python main.py

Env:
  HTTP_HOST=0.0.0.0
  HTTP_PORT=8001
  MODBUS_LISTEN_PORT=502
  STRICT_TELEGRAM=1
  STRICT_OBJECT_SIZE=1
  DEBUG_MBAP=0
  SOCKET_RECV_TIMEOUT_S=2.0
"""

import os
import json
import time
import socket
import struct
import threading
from enum import IntEnum
from typing import Optional
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

EMULATOR_VERSION = "tidfix-2026-01-29-ds402bit"

HTTP_HOST = os.getenv("HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.getenv("HTTP_PORT", "8001"))
MODBUS_LISTEN_PORT = int(os.getenv("MODBUS_LISTEN_PORT", "502"))

STRICT_TELEGRAM = os.getenv("STRICT_TELEGRAM", "1").lower() not in ("0", "false", "no")
STRICT_OBJECT_SIZE = os.getenv("STRICT_OBJECT_SIZE", "1").lower() not in ("0", "false", "no")
DEBUG_MBAP = os.getenv("DEBUG_MBAP", "0").lower() in ("1", "true", "yes")
SOCKET_RECV_TIMEOUT_S = float(os.getenv("SOCKET_RECV_TIMEOUT_S", "2.0"))

# Known object sizes (bytes) used for strict checking.
OBJECT_SIZES = {
    0x6040: 2,  # Controlword
    0x6041: 2,  # Statusword
    0x6060: 1,  # Mode of operation
    0x6061: 1,  # Mode of operation display
    0x6064: 4,  # Position actual value
    0x606C: 4,  # Velocity actual value
    0x607A: 4,  # Target position
    0x6081: 4,  # Profile velocity
    0x6083: 4,  # Profile acceleration
    0x6084: 4,  # Profile deceleration (often used by clients)
    0x60FF: 4,  # Target velocity
    0x6098: 1,  # Homing method (client may write 0x23 etc.)
    0x607B: 4,  # Software position limit min (common pattern in your logs)
    0x607D: 4,  # Software position limit max (common pattern in your logs)
    0x2014: 2,  # custom diag/status code (legacy: 1 OK, 0x23 homing running)
}

SIGNED_32 = {0x6064, 0x606C, 0x607A, 0x6081, 0x6083, 0x6084, 0x60FF, 0x607B, 0x607D}
SIGNED_8 = {0x6060, 0x6061}

# DS402 statusword bits (minimal set)
SW_READY_TO_SWITCH_ON = 1 << 0
SW_SWITCHED_ON = 1 << 1
SW_OPERATION_ENABLED = 1 << 2
SW_FAULT = 1 << 3
SW_VOLTAGE_ENABLED = 1 << 4
SW_QUICK_STOP = 1 << 5
SW_SWITCH_ON_DISABLED = 1 << 6
SW_WARNING = 1 << 7
SW_REMOTE = 1 << 9
SW_TARGET_REACHED = 1 << 10
SW_INTERNAL_LIMIT_ACTIVE = 1 << 11
SW_HOMING_ATTAINED = 1 << 12
SW_HOMING_ERROR = 1 << 13


class DS402State(IntEnum):
    SWITCH_ON_DISABLED = 0
    READY_TO_SWITCH_ON = 1
    SWITCHED_ON = 2
    OPERATION_ENABLED = 3
    FAULT = 4
    QUICK_STOP_ACTIVE = 5


class ClientRegistry:
    def __init__(self):
        self.lock = threading.Lock()
        self.clients = {}

    def update(self, client_id, **kwargs):
        with self.lock:
            self.clients.setdefault(client_id, {})
            self.clients[client_id].update(kwargs)
            self.clients[client_id]["last_seen"] = time.time()

    def remove(self, client_id):
        with self.lock:
            if client_id in self.clients:
                self.clients[client_id]["status"] = "offline"
                self.clients[client_id]["last_seen"] = time.time()

    def all(self):
        with self.lock:
            now = time.time()
            out = []
            for cid, info in self.clients.items():
                status = info.get("status", "offline")
                if status == "online" and now - info["last_seen"] > 5:
                    status = "offline"
                out.append(
                    {
                        "id": cid,
                        "type": info.get("type", "unknown"),
                        "address": info.get("address", ""),
                        "status": status,
                        "last_seen": info["last_seen"],
                    }
                )
            return out


CLIENTS = ClientRegistry()


class FakeDriveState:
    """
    Minimal, practical DS402-ish drive state that matches typical client behavior.
    """

    def __init__(self):
        self._lock = threading.RLock()

        # Core DS402 state
        self.state: DS402State = DS402State.SWITCH_ON_DISABLED
        self.fault: bool = False
        self.warning: bool = False

        # Operation modes
        self.op_mode: int = 0  # 0x6060
        self.op_mode_display: int = 0  # 0x6061 MUST mirror 6060

        # Motion / process values
        self.position: int = 0
        self.velocity: int = 0
        self.target_position: int = 0
        self.profile_velocity: int = 5000
        self.acceleration: int = 2500
        self.deceleration: int = 2500
        self.target_velocity: int = 0  # 0x60FF

        self.is_moving: bool = False
        self.target_reached: bool = True

        # Homing
        self.homed: bool = False
        self.homing_error: bool = False
        self.homing_method: int = 0  # 0x6098 (some clients write here)
        self.diag_code: int = 1  # 0x2014, legacy compatibility

        # Soft limits (used by your driver)
        self.soft_limit_min: int = 0
        self.soft_limit_max: int = 120000

        # Emergency
        self.emergency_active: bool = False

        # Controlword edge detection (bit4)
        self._last_controlword: int = 0

        # Motion engine (single source of truth for 0x6064/0x606C)
        self._engine_running: bool = True
        self._engine_thread: Optional[threading.Thread] = None

        # Active motion mode inside engine
        self._pv_active: bool = False              # Profile velocity active
        self._pp_active: bool = False              # Profile position active
        self._homing_active: bool = False          # Homing active

        self._pp_goal: int = 0                     # absolute goal position
        self._pp_relative: bool = False
        self._pp_start_pos: int = 0
        self._pp_started_at: float = 0.0

        self._homing_started_at: float = 0.0
        self._homing_duration_s: float = 0.8

        # Fallback OD storage
        self._od = {}
        self._od_lock = threading.RLock()

        # start engine
        self._start_engine()

    # --------------------------- DS402 helpers ---------------------------

    def _set_state(self, new_state: DS402State) -> None:
        self.state = new_state
        if new_state == DS402State.FAULT:
            self.fault = True

    def _decode_controlword(self, cw: int):
        """
        Minimal bit decoding to accept common client sequences like:
        0x0006, 0x0007, 0x000F and also 0x000B, 0x002F, 0x003F etc.
        """
        shutdown_cmd = (cw & 0x000F) in (0x0006, 0x000E) or ((cw & 0x0006) == 0x0006)
        switch_on_cmd = (cw & 0x000F) in (0x0007, 0x000F) or ((cw & 0x0007) == 0x0007)
        enable_op_cmd = (cw & 0x000F) == 0x000F  # operation enabled "mask"

        # CiA 402 quick stop: bit2=0 while bits 0,1 remain set.
        # Pattern: xxxx.xxxx.0xxx.x11x  →  (cw & 0x0007) == 0x0003
        # Also accept legacy value 0x0002 used by old dryve D1 driver.
        quick_stop_cmd = ((cw & 0x0007) == 0x0003) or (cw & 0x000F) == 0x0002

        fault_reset = bool(cw & (1 << 7))
        halt = bool(cw & (1 << 8))  # "halt" in many implementations
        new_setpoint = bool(cw & (1 << 4))

        # relative/abs selection: bit6 in DS402 profile position
        relative = bool(cw & (1 << 6))

        return shutdown_cmd, switch_on_cmd, enable_op_cmd, fault_reset, halt, new_setpoint, quick_stop_cmd, relative

    def _apply_controlword(self, cw: int) -> None:
        with self._lock:
            shutdown_cmd, switch_on_cmd, enable_op_cmd, fault_reset, halt, new_sp, quick_stop_cmd, relative = self._decode_controlword(cw)

            last_new_sp = bool(self._last_controlword & (1 << 4))
            rising_edge_new_sp = (new_sp and not last_new_sp)
            self._last_controlword = cw

            if fault_reset:
                self.fault = False
                self.homing_error = False
                self.diag_code = 1
                self._set_state(DS402State.SWITCH_ON_DISABLED)
                self._stop_all_motion_locked()
                self.target_reached = True
                return

            if self.emergency_active:
                self._stop_all_motion_locked()
                return

            if halt:
                self._stop_all_motion_locked()
                self.target_reached = True
                return

            # Quick stop: stop all motion and transition to READY_TO_SWITCH_ON
            if quick_stop_cmd and self.state in (DS402State.OPERATION_ENABLED, DS402State.QUICK_STOP_ACTIVE):
                self._stop_all_motion_locked()
                self.target_reached = True
                self._set_state(DS402State.READY_TO_SWITCH_ON)
                return

            # State transitions (simplified)
            if self.fault:
                self._set_state(DS402State.FAULT)
                return

            if shutdown_cmd:
                self._set_state(DS402State.READY_TO_SWITCH_ON)

            if switch_on_cmd and self.state >= DS402State.READY_TO_SWITCH_ON:
                self._set_state(DS402State.SWITCHED_ON)

            if enable_op_cmd and self.state >= DS402State.SWITCHED_ON:
                self._set_state(DS402State.OPERATION_ENABLED)

            # Start motion by rising edge of bit4 ONLY when op enabled and mode accepted
            if rising_edge_new_sp and self.state == DS402State.OPERATION_ENABLED and self.op_mode_display == self.op_mode:
                if self.op_mode == 6:  # Homing
                    self._start_homing_locked()
                elif self.op_mode == 1:  # Profile position
                    self._start_profile_position_locked(relative=relative)
                elif self.op_mode == 3:  # Profile velocity
                    self._start_profile_velocity_locked()
                else:
                    # Unknown mode: ignore
                    pass

    def _start_engine(self):
        if self._engine_thread and self._engine_thread.is_alive():
            return

        def loop():
            last = time.time()
            while True:
                now = time.time()
                dt = now - last
                if dt < 0:
                    dt = 0
                if dt > 0.1:
                    dt = 0.1
                last = now

                with self._lock:
                    if not self._engine_running:
                        return
                    if self.emergency_active:
                        # hard stop
                        self._pv_active = False
                        self._pp_active = False
                        self._homing_active = False
                        self.target_velocity = 0
                        self.velocity = 0
                        self.is_moving = False
                        self.target_reached = True
                        # keep homing flags as-is
                        time.sleep(0.02)
                        continue

                    # If not operation enabled -> no motion
                    if self.state != DS402State.OPERATION_ENABLED:
                        self._pv_active = False
                        self._pp_active = False
                        self._homing_active = False
                        self.target_velocity = 0
                        self.velocity = 0
                        self.is_moving = False
                        self.target_reached = True
                        time.sleep(0.02)
                        continue

                    # ---- Homing ----
                    if self._homing_active and self.op_mode == 6:
                        t = now - self._homing_started_at
                        if t >= self._homing_duration_s:
                            self.position = 0
                            self.velocity = 0
                            self.is_moving = False
                            self.target_reached = True
                            self.homed = True
                            self.homing_error = False
                            self.diag_code = 1
                            self._homing_active = False
                        else:
                            # simple ramp to zero
                            # move toward 0 at profile_velocity units/s
                            v = max(1, min(abs(int(self.profile_velocity) or 5000), 20000))
                            direction = -1 if self.position > 0 else (1 if self.position < 0 else 0)
                            step = int(direction * v * dt)
                            # ensure we don't overshoot
                            if direction < 0 and self.position + step < 0:
                                self.position = 0
                            elif direction > 0 and self.position + step > 0:
                                self.position = 0
                            else:
                                self.position += step
                            self.velocity = int(direction * v) if direction != 0 else 0
                            self.is_moving = (self.velocity != 0)
                            self.target_reached = False
                        self._clamp_to_soft_limits_locked()
                        time.sleep(0.02)
                        continue

                    # ---- Profile Position ----
                    if self._pp_active and self.op_mode == 1:
                        goal = int(self._pp_goal)
                        cur = int(self.position)
                        if cur == goal:
                            self.velocity = 0
                            self.is_moving = False
                            self.target_reached = True
                            self._pp_active = False
                            time.sleep(0.02)
                            continue

                        v = max(1, min(abs(int(self.profile_velocity) or 5000), 20000))
                        direction = 1 if goal > cur else -1
                        step = int(direction * v * dt)
                        # minimum step to show progress if dt is tiny
                        if step == 0:
                            step = direction
                        nxt = cur + step
                        # clamp overshoot
                        if direction > 0 and nxt > goal:
                            nxt = goal
                        if direction < 0 and nxt < goal:
                            nxt = goal

                        self.position = int(nxt)
                        self.velocity = int(direction * v) if self.position != goal else 0
                        self.is_moving = (self.position != goal)
                        self.target_reached = (self.position == goal)
                        if self.target_reached:
                            self.velocity = 0
                            self.is_moving = False
                            self._pp_active = False

                        self._clamp_to_soft_limits_locked()
                        time.sleep(0.02)
                        continue

                    # ---- Profile Velocity ----
                    if self._pv_active and self.op_mode == 3:
                        tv = int(self.target_velocity)
                        # simple 1st order: instant follow (can add ramp later)
                        self.velocity = tv
                        self.is_moving = (tv != 0)
                        self.target_reached = (tv == 0)
                        if tv != 0:
                            self.position += int(tv * dt)
                            self._clamp_to_soft_limits_locked()
                        time.sleep(0.02)
                        continue

                    # If no active mode: keep actual velocity at 0
                    self.velocity = 0
                    self.is_moving = False
                    self.target_reached = True
                time.sleep(0.02)

        self._engine_thread = threading.Thread(target=loop, daemon=True)
        self._engine_thread.start()

    # --------------------------- Motion implementations ---------------------------

    def _stop_all_motion_locked(self):
        self._pv_active = False
        self._pp_active = False
        self._homing_active = False
        self.target_velocity = 0
        self.velocity = 0
        self.is_moving = False

    # (jog thread removed; engine handles PV)

    def _clamp_to_soft_limits_locked(self):
        if self.position < self.soft_limit_min:
            self.position = self.soft_limit_min
        if self.position > self.soft_limit_max:
            self.position = self.soft_limit_max

    def _start_profile_velocity_locked(self):
        # Start PV only on CW bit4 rising edge (already ensured by caller).
        # If target_velocity is 0 => treat as stop.
        if int(self.target_velocity) == 0:
            self._pv_active = False
            self.velocity = 0
            self.is_moving = False
            self.target_reached = True
            return
        self._pp_active = False
        self._homing_active = False
        self._pv_active = True
        self.target_reached = False

    def _start_profile_position_locked(self, *, relative: bool):
        start = int(self.position)
        end = int(start + self.target_position) if relative else int(self.target_position)
        end = max(self.soft_limit_min, min(self.soft_limit_max, end))

        self._pv_active = False
        self._homing_active = False
        self._pp_active = True
        self._pp_goal = int(end)
        self._pp_relative = bool(relative)
        self._pp_start_pos = int(start)
        self._pp_started_at = time.time()
        self.is_moving = True
        self.target_reached = False

    def _start_homing_locked(self):
        self._pv_active = False
        self._pp_active = False
        self._homing_active = True
        self.homed = False
        self.homing_error = False
        self.diag_code = 0x23
        self.is_moving = True
        self.target_reached = False

        self._homing_started_at = time.time()
        # Make homing duration somewhat proportional to distance but bounded
        v = max(1, min(abs(int(self.profile_velocity) or 5000), 20000))
        dist = abs(int(self.position))
        dur = dist / float(v) if v > 0 else 0.5
        self._homing_duration_s = max(0.3, min(dur, 1.5))

    # --------------------------- SDO read/write ---------------------------

    def make_statusword(self) -> int:
        with self._lock:
            sw = 0

            # Quick minimal mapping by state
            if self.state == DS402State.SWITCH_ON_DISABLED:
                sw |= SW_SWITCH_ON_DISABLED
            elif self.state == DS402State.READY_TO_SWITCH_ON:
                sw |= SW_READY_TO_SWITCH_ON | SW_QUICK_STOP | SW_VOLTAGE_ENABLED
            elif self.state == DS402State.SWITCHED_ON:
                sw |= SW_READY_TO_SWITCH_ON | SW_SWITCHED_ON | SW_QUICK_STOP | SW_VOLTAGE_ENABLED
            elif self.state == DS402State.OPERATION_ENABLED:
                sw |= (
                    SW_READY_TO_SWITCH_ON
                    | SW_SWITCHED_ON
                    | SW_OPERATION_ENABLED
                    | SW_QUICK_STOP
                    | SW_VOLTAGE_ENABLED
                )
            elif self.state == DS402State.FAULT:
                sw |= SW_FAULT | SW_SWITCH_ON_DISABLED
            elif self.state == DS402State.QUICK_STOP_ACTIVE:
                # Quick stop active: bits 0,1,4,5 set, bit 2 (qs) CLEARED
                sw |= SW_READY_TO_SWITCH_ON | SW_SWITCHED_ON | SW_VOLTAGE_ENABLED

            if self.fault:
                sw |= SW_FAULT
            if self.warning:
                sw |= SW_WARNING

            # Many clients expect "remote" bit set
            sw |= SW_REMOTE

            # Target reached bit: set when not moving
            if self.target_reached and not self.is_moving:
                sw |= SW_TARGET_REACHED

            # Homing bits in homing mode
            if self.op_mode == 6:
                if self.homed:
                    sw |= SW_HOMING_ATTAINED
                if self.homing_error:
                    sw |= SW_HOMING_ERROR

            return sw & 0xFFFF

    def sdo_read(self, index_hi: int, index_lo: int, subindex: int, length: int) -> bytes:
        idx = (index_hi << 8) | index_lo

        with self._lock:
            if idx == 0x6041:
                return struct.pack("<H", self.make_statusword())
            if idx == 0x2014:
                return struct.pack("<H", int(self.diag_code) & 0xFFFF)
            if idx == 0x6064:
                return struct.pack("<i", int(self.position))
            if idx == 0x606C:
                return struct.pack("<i", int(self.velocity))
            if idx == 0x6060:
                return struct.pack("<b", int(self.op_mode) & 0xFF)
            if idx == 0x6061:
                return struct.pack("<b", int(self.op_mode_display) & 0xFF)
            if idx == 0x607A:
                return struct.pack("<i", int(self.target_position))
            if idx == 0x6081:
                return struct.pack("<i", int(self.profile_velocity))
            if idx == 0x6083:
                return struct.pack("<i", int(self.acceleration))
            if idx == 0x6084:
                return struct.pack("<i", int(self.deceleration))
            if idx == 0x60FF:
                return struct.pack("<i", int(self.target_velocity))
            if idx == 0x6098:
                return struct.pack("<B", int(self.homing_method) & 0xFF)
            if idx == 0x607B:
                return struct.pack("<i", int(self.soft_limit_min))
            if idx == 0x607D:
                return struct.pack("<i", int(self.soft_limit_max))

        # fallback OD
        with self._od_lock:
            raw = self._od.get((idx, subindex), bytes([0] * length))
        if len(raw) >= length:
            return raw[:length]
        return raw + bytes([0] * (length - len(raw)))

    def sdo_write(self, index_hi: int, index_lo: int, subindex: int, raw: bytes) -> None:
        idx = (index_hi << 8) | index_lo

        # store raw in OD for unknown objects
        with self._od_lock:
            self._od[(idx, subindex)] = raw

        with self._lock:
            if idx == 0x6060:
                # Mode of operation; MUST mirror to display in real behavior
                self.op_mode = struct.unpack("<b", raw[:1])[0]
                self.op_mode_display = self.op_mode
                return

            if idx == 0x607A:
                self.target_position = struct.unpack("<i", raw[:4].ljust(4, b"\x00"))[0]
                return

            if idx == 0x6081:
                self.profile_velocity = struct.unpack("<i", raw[:4].ljust(4, b"\x00"))[0]
                return

            if idx == 0x6083:
                self.acceleration = struct.unpack("<i", raw[:4].ljust(4, b"\x00"))[0]
                return

            if idx == 0x6084:
                self.deceleration = struct.unpack("<i", raw[:4].ljust(4, b"\x00"))[0]
                return

            if idx == 0x60FF:
                self.target_velocity = struct.unpack("<i", raw[:4].ljust(4, b"\x00"))[0]
                # Do NOT start motion here; start happens on CW bit4 rising edge.
                return

            if idx == 0x6098:
                self.homing_method = struct.unpack("<B", raw[:1])[0]
                return

            if idx == 0x607B:
                self.soft_limit_min = struct.unpack("<i", raw[:4].ljust(4, b"\x00"))[0]
                # clamp current pos if needed
                self._clamp_to_soft_limits_locked()
                return

            if idx == 0x607D:
                self.soft_limit_max = struct.unpack("<i", raw[:4].ljust(4, b"\x00"))[0]
                self._clamp_to_soft_limits_locked()
                return

            if idx == 0x6040:
                cw = struct.unpack("<H", raw[:2].ljust(2, b"\x00"))[0]
                self._apply_controlword(cw)
                return

            # else: ignore


fakeDrive = FakeDriveState()

# ----------------------------- HTTP server -----------------------------


class EmulatorHTTPRequestHandler(SimpleHTTPRequestHandler):
    """Serve static files and stream drive state via SSE."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.dirname(__file__), **kwargs)

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/version":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(json.dumps({"version": EMULATOR_VERSION}).encode("utf-8"))
            return

        if self.path == "/clients":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(json.dumps(CLIENTS.all()).encode("utf-8"))
            return

        if self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                while True:
                    with fakeDrive._lock:
                        result = {
                            "position": fakeDrive.position,
                            "target_position": fakeDrive.target_position,
                            "velocity": fakeDrive.velocity,
                            "target_velocity": fakeDrive.target_velocity,
                            "acceleration": fakeDrive.acceleration,
                            "deceleration": fakeDrive.deceleration,
                            "homed": fakeDrive.homed,
                            "homing_error": fakeDrive.homing_error,
                            "mode": fakeDrive.op_mode,
                            "mode_display": fakeDrive.op_mode_display,
                            "is_moving": fakeDrive.is_moving,
                            "target_reached": fakeDrive.target_reached,
                            "emergency_active": fakeDrive.emergency_active,
                            "state": int(fakeDrive.state),
                            "fault": fakeDrive.fault,
                            "diag_code": fakeDrive.diag_code,
                            "statusword": fakeDrive.make_statusword(),
                            "soft_limit_min": fakeDrive.soft_limit_min,
                            "soft_limit_max": fakeDrive.soft_limit_max,
                        }
                    data = json.dumps(result)
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(0.05)
            except Exception:
                pass
            return

        super().do_GET()

    def do_POST(self):
        if self.path == "/emergency":
            with fakeDrive._lock:
                fakeDrive.emergency_active = not fakeDrive.emergency_active
                if fakeDrive.emergency_active:
                    fakeDrive._stop_all_motion_locked()
            self.send_response(200)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()


def start_http_server():
    server = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), EmulatorHTTPRequestHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"HTTP server started at http://{HTTP_HOST}:{HTTP_PORT}")


# ----------------------------- Modbus gateway -----------------------------


def _recvall(conn: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf.extend(chunk)
    return bytes(buf)


def read_modbus_frame(conn: socket.socket) -> bytes:
    """
    Read exactly one Modbus TCP frame:
      6 bytes MBAP header (tid+pid+len) + `len` bytes body (unit + PDU)
    """
    header = _recvall(conn, 6)
    tid, pid, length = struct.unpack(">HHH", header)
    if length < 2 or length > 260:
        raise ValueError(f"invalid MBAP length: {length}")
    body = _recvall(conn, length)
    return header + body


def build_mbap(tid_bytes: bytes, payload_len: int) -> bytes:
    if len(tid_bytes) != 2:
        raise ValueError("tid_bytes must be length 2")
    # pid=0, len=payload_len
    return tid_bytes + b"\x00\x00" + struct.pack(">H", payload_len)


def build_exception(tid_bytes: bytes, unit_id: int, func: int, exception_code: int) -> bytes:
    pdu = bytes([unit_id, func | 0x80, exception_code & 0xFF])
    mbap = build_mbap(tid_bytes, len(pdu))
    return mbap + pdu


def build_gateway_response(
    tid_bytes: bytes,
    unit_id: int,
    proto_control: int,
    index_hi: int,
    index_lo: int,
    subindex: int,
    byte_count: int,
    data: bytes,
    *,
    is_write_handshake: bool,
) -> bytes:
    if is_write_handshake:
        # response for write: length = 0x0D, byte_count = 0, no data
        bc = 0
        payload = bytes(
            [
                unit_id,
                0x2B,
                0x0D,
                proto_control & 0xFF,
                0x00,  # reserved10
                0x00,  # node_id
                index_hi & 0xFF,
                index_lo & 0xFF,
                subindex & 0xFF,
                0x00,  # start addr hi
                0x00,  # start addr lo
                0x00,  # reserved17
                bc,  # byte count
            ]
        )
        mbap = build_mbap(tid_bytes, len(payload))
        return mbap + payload

    # read response: length = 0x0D + byte_count, includes data bytes
    payload = bytes(
        [
            unit_id,
            0x2B,
            0x0D,
            proto_control & 0xFF,
            0x00,
            0x00,
            index_hi & 0xFF,
            index_lo & 0xFF,
            subindex & 0xFF,
            0x00,
            0x00,
            0x00,
            byte_count & 0xFF,
        ]
    ) + data
    mbap = build_mbap(tid_bytes, len(payload))
    return mbap + payload


def parse_gateway_request(frame: bytes):
    """
    Returns dict with parsed fields.
    Raises ValueError for telegram structure issues.
    Returns a dict with `not_gateway=True` if func != 0x2B.
    """
    if len(frame) < 19:
        raise ValueError("frame too short")

    tid, pid, length = struct.unpack(">HHH", frame[:6])
    tid_bytes = frame[0:2]
    if pid != 0:
        raise ValueError("protocol id must be 0")

    if len(frame) != 6 + length:
        raise ValueError("frame length mismatch")

    unit_id = frame[6]
    func = frame[7]

    if func != 0x2B:
        return {"tid": tid, "tid_bytes": tid_bytes, "unit_id": unit_id, "func": func, "not_gateway": True}

    if length < 13:
        raise ValueError("gateway body too short")

    mei = frame[8]
    proto_control = frame[9]
    reserved10 = frame[10]
    node_id = frame[11]
    index_hi = frame[12]
    index_lo = frame[13]
    subindex = frame[14]
    start15 = frame[15]
    start16 = frame[16]
    reserved17 = frame[17]
    byte_count = frame[18]

    if mei != 0x0D:
        raise ValueError("MEI must be 0x0D")

    if proto_control not in (0, 1):
        raise ValueError("protocol control must be 0 or 1")

    if byte_count < 1 or byte_count > 4:
        raise ValueError("byte_count must be 1..4")

    if STRICT_TELEGRAM:
        if reserved10 != 0 or node_id != 0 or start15 != 0 or start16 != 0 or reserved17 != 0:
            raise ValueError("reserved bytes must be 0")

    if proto_control == 0:
        # READ request must have length exactly 0x0D
        if length != 13:
            raise ValueError("read request length must be 0x0D")
        data = b""
    else:
        # WRITE request must have length 0x0D + byte_count
        if length != 13 + byte_count:
            raise ValueError("write request length must be 0x0D + byte_count")
        data = frame[19 : 19 + byte_count]
        if len(data) != byte_count:
            raise ValueError("write data length mismatch")

    idx = (index_hi << 8) | index_lo
    if STRICT_OBJECT_SIZE and idx in OBJECT_SIZES and byte_count != OBJECT_SIZES[idx]:
        raise ValueError(f"byte_count does not match object size for 0x{idx:04X}")

    return {
        "tid": tid,
        "tid_bytes": tid_bytes,
        "unit_id": unit_id,
        "func": func,
        "mei": mei,
        "proto_control": proto_control,
        "index_hi": index_hi,
        "index_lo": index_lo,
        "subindex": subindex,
        "byte_count": byte_count,
        "data": data,
    }


modbus_client_lock = threading.Lock()


def modbus_handle_client(conn: socket.socket, state: FakeDriveState):
    client_addr = conn.getpeername()
    client_id = f"modbus:{client_addr[0]}:{client_addr[1]}"

    acquired = modbus_client_lock.acquire(blocking=False)
    if not acquired:
        try:
            conn.close()
        except Exception:
            pass
        print(f"[Modbus] Refused second connection from {client_addr} — already in use.")
        return

    conn.settimeout(SOCKET_RECV_TIMEOUT_S)
    CLIENTS.update(client_id, type="modbus", address=f"{client_addr[0]}:{client_addr[1]}", status="online")

    try:
        while True:
            try:
                frame = read_modbus_frame(conn)
                if DEBUG_MBAP:
                    print(f"[MBAP] rx from {client_addr} raw={frame[:min(len(frame),64)].hex()} len={len(frame)}")
            except socket.timeout:
                continue
            except (ConnectionError, OSError):
                break
            except ValueError as e:
                # Bad MBAP length etc. -> close
                print(f"[Modbus] Frame error from {client_addr}: {e}")
                break

            CLIENTS.update(client_id, status="online")

            try:
                req = parse_gateway_request(frame)
            except ValueError as e:
                # Telegram structure error -> Illegal Data Value (03) and close
                try:
                    if len(frame) >= 8:
                        unit = frame[6]
                        func = frame[7]
                        conn.sendall(build_exception(frame[0:2], unit, func, 0x03))
                except Exception:
                    pass
                print(f"[Modbus] Telegram structure error from {client_addr}: {e}")
                break

            if req.get("not_gateway"):
                # Unsupported function code -> Illegal Function (01)
                resp = build_exception(req["tid_bytes"], req["unit_id"], req["func"], 0x01)
                if DEBUG_MBAP:
                    print(f"[MBAP] tx to {client_addr} raw={resp[:min(len(resp),64)].hex()} len={len(resp)}")
                conn.sendall(resp)
                continue

            tid_bytes = req["tid_bytes"]
            unit_id = req["unit_id"]
            proto_control = req["proto_control"]
            index_hi = req["index_hi"]
            index_lo = req["index_lo"]
            subindex = req["subindex"]
            byte_count = req["byte_count"]

            if proto_control == 0:
                raw = state.sdo_read(index_hi, index_lo, subindex, byte_count)
                # enforce exact byte_count
                if len(raw) < byte_count:
                    raw = raw + b"\x00" * (byte_count - len(raw))
                elif len(raw) > byte_count:
                    raw = raw[:byte_count]

                resp = build_gateway_response(
                    tid_bytes,
                    unit_id,
                    proto_control,
                    index_hi,
                    index_lo,
                    subindex,
                    byte_count,
                    raw,
                    is_write_handshake=False,
                )
                if DEBUG_MBAP:
                    print(f"[MBAP] tx to {client_addr} raw={resp[:min(len(resp),64)].hex()} len={len(resp)}")
                conn.sendall(resp)
            else:
                data = req["data"]
                # WRITE: pass raw bytes directly to OD handler
                state.sdo_write(index_hi, index_lo, subindex, data)

                resp = build_gateway_response(
                    tid_bytes,
                    unit_id,
                    proto_control,
                    index_hi,
                    index_lo,
                    subindex,
                    byte_count,
                    b"",
                    is_write_handshake=True,
                )
                if DEBUG_MBAP:
                    print(f"[MBAP] tx to {client_addr} raw={resp[:min(len(resp),64)].hex()} len={len(resp)}")
                conn.sendall(resp)

    finally:
        try:
            conn.close()
        except Exception:
            pass
        CLIENTS.remove(client_id)
        modbus_client_lock.release()
        print(f"[Modbus] Disconnected client {client_addr}, slot freed.")


def start_modbus_server():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((HTTP_HOST, MODBUS_LISTEN_PORT))
    s.listen(5)
    print(f"Modbus TCP Gateway emulator listening on {HTTP_HOST}:{MODBUS_LISTEN_PORT}")
    while True:
        conn, _addr = s.accept()
        threading.Thread(target=modbus_handle_client, args=(conn, fakeDrive), daemon=True).start()


def main():
    threading.Thread(target=start_http_server, daemon=True).start()
    threading.Thread(target=start_modbus_server, daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
