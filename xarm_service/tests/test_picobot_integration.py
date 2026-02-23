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

    def getset_tgpio_modbus_data(self, command, **kwargs):
        self.modbus_calls.append((list(command), kwargs))
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

    def test_disable_sdk_vacuum_reads_after_exception_c19(self):
        api = _MockApi()
        api._vacuum_mode = "exception19"
        gc = GripperController(api)

        first = gc.read_vacuum_via_sdk()
        second = gc.read_vacuum_via_sdk()

        assert first == -99
        assert second == -99
        assert gc.sdk_vacuum_supported is False
        assert api.vacuum_calls == 1

    def test_disable_sdk_vacuum_reads_after_code19(self):
        api = _MockApi()
        api._vacuum_mode = "code19"
        gc = GripperController(api)

        first = gc.read_vacuum_via_sdk()
        second = gc.read_vacuum_via_sdk()

        assert first == -99
        assert second == -99
        assert gc.sdk_vacuum_supported is False
        assert api.vacuum_calls == 1

    def test_disable_after_repeated_nonzero_code(self):
        api = _MockApi()
        api._vacuum_mode = "other"
        gc = GripperController(api)

        first = gc.read_vacuum_via_sdk()
        second = gc.read_vacuum_via_sdk()
        third = gc.read_vacuum_via_sdk()

        assert first == -99
        assert second == -99
        assert third == -99
        assert gc.sdk_vacuum_supported is False
        assert gc.sdk_vacuum_disabled_reason == "persistent_nonzero_code_1"
        assert api.vacuum_calls == 2

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
