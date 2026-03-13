"""Unit tests for piCOBOT gripper integration hardening (C19/degraded mode)."""
import os
import sys
import importlib.util

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

from models.grasp_types import GripperFeedback


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_pb = _load_module("picobot_lib_integration", "drivers/xarm_driver/picobot_lib.py")
GripperController = _pb.GripperController
_EXPECTED_OK = _pb._EXPECTED_OK


class _MockApi:
    def __init__(self):
        self.baudrate_calls = []
        self.timeout_calls = []
        self.modbus_calls = []
        self.vacuum_calls = 0
        self._vacuum_mode = "ok"

    def set_tgpio_modbus_baudrate(self, value):
        self.baudrate_calls.append(value)
        return 0

    def set_tgpio_modbus_timeout(self, value):
        self.timeout_calls.append(value)
        return 0

    # Valid PDI frame: index=0x0028, data_len=5, part_secured=True, CRC-valid
    _PDI_RESPONSE = [1, 13, 4, 0, 0, 0, 40, 5, 0, 0, 2, 0, 0, 25, 76]

    def getset_tgpio_modbus_data(self, command, **kwargs):
        self.modbus_calls.append((list(command), kwargs))
        # Return valid PDI frame for PDI read command, _EXPECTED_OK otherwise
        if list(command) == [0x01, 0x04, 0x00, 0x00, 0x00, 0x28, 0xD8]:
            return 0, list(self._PDI_RESPONSE)
        return 0, list(_EXPECTED_OK)

    def get_vacuum_gripper(self):
        self.vacuum_calls += 1
        if self._vacuum_mode == "ok":
            return 0, 1
        if self._vacuum_mode == "code19":
            return 19, 0
        if self._vacuum_mode == "exception19":
            raise RuntimeError("ControllerError, code: 19")
        return 1, 0

    def get_joints_torque(self):
        return 0, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


class TestPiCoBotIntegration:
    def test_modbus_config_applied_on_init(self):
        api = _MockApi()
        GripperController(api, baudrate=115200, timeout=100)
        assert api.baudrate_calls == [115200]
        assert api.timeout_calls == [100]

    def test_pdi_disabled_after_persistent_failures(self):
        """PDI reads are disabled after consecutive failures (fail streak limit)."""
        api = _MockApi()
        gc = GripperController(api)

        # Force PDI to fail by making getset_tgpio_modbus_data return empty
        original_fn = api.getset_tgpio_modbus_data
        def _fail_pdi(command, **kwargs):
            if command == [0x01, 0x04, 0x00, 0x00, 0x00, 0x28, 0xD8]:
                return 0, []
            return original_fn(command, **kwargs)
        api.getset_tgpio_modbus_data = _fail_pdi

        # Exhaust the fail streak limit (default 5)
        for _ in range(6):
            status = gc.get_status()

        assert gc.pdi_supported is False
        assert gc.pdi_disabled_reason is not None
        assert status.sensor_supported is False

    def test_activate_sets_active_and_uses_modbus(self):
        api = _MockApi()
        gc = GripperController(api)

        raw = gc.activate()

        assert raw == _EXPECTED_OK
        assert gc.is_active is True
        assert len(api.modbus_calls) == 1

    def test_get_status_maps_feedback(self):
        api = _MockApi()
        gc = GripperController(api)

        status = gc.get_status()

        assert status.feedback == GripperFeedback.PART_GRIPPED
