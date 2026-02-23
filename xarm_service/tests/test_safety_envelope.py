"""Unit tests for SafetyEnvelope and boundary (in/out cases)."""
import pytest
from drivers.xarm_driver.safety import (
    SafetyEnvelope,
    CheckResult,
    Violation,
    compute_base_boundary_mm,
    build_boundary_list,
)


def test_compute_base_boundary():
    # WS 400x900x1200, base at (150, 450, 0), margin 40
    x_min, x_max, y_min, y_max, z_min, z_max = compute_base_boundary_mm(
        (400, 900, 1200), (150, 450, 0), 40
    )
    assert x_min == -150 + 40  # -110
    assert x_max == 400 - 150 - 40  # 210
    assert y_min == -450 + 40  # -410
    assert y_max == 900 - 450 - 40  # 410
    assert z_min == 0 + 40  # 40
    assert z_max == 1200 - 0 - 40  # 1160


def test_build_boundary_list():
    lst = build_boundary_list((400, 900, 1200), (150, 450, 0), 40)
    assert lst == [210, -110, 410, -410, 1160, 40]  # x_max, x_min, y_max, y_min, z_max, z_min


def test_envelope_tcp_inside():
    env = SafetyEnvelope((400, 900, 1200), (150, 450, 0), 40)
    # Center of box in base frame: (50, 0, 600)
    r = env.check_tcp_in_ws([50, 0, 600])
    assert r.ok is True
    assert r.violations == []


def test_envelope_tcp_outside_x_max():
    env = SafetyEnvelope((400, 900, 1200), (150, 450, 0), 40)
    r = env.check_tcp_in_ws([300, 0, 600])  # x=300 > x_max=210
    assert r.ok is False
    assert len(r.violations) >= 1
    assert any(v.axis == "x_max" for v in r.violations)


def test_envelope_tcp_outside_x_min():
    env = SafetyEnvelope((400, 900, 1200), (150, 450, 0), 40)
    r = env.check_tcp_in_ws([-200, 0, 600])  # x=-200 < x_min=-110
    assert r.ok is False
    assert any(v.axis == "x_min" for v in r.violations)


def test_envelope_tcp_outside_z_min():
    env = SafetyEnvelope((400, 900, 1200), (150, 450, 0), 40)
    r = env.check_tcp_in_ws([50, 0, 0])  # z=0 < z_min=40
    assert r.ok is False
    assert any(v.axis == "z_min" for v in r.violations)


def test_envelope_check_points_in_ws():
    env = SafetyEnvelope((400, 900, 1200), (150, 450, 0), 40)
    r = env.check_points_in_ws([("tcp", 50, 0, 600), ("link2", 0, 0, 300)])
    assert r.ok is True
    r2 = env.check_points_in_ws([("tcp", 50, 0, 600), ("link2", 300, 0, 300)])
    assert r2.ok is False


def test_trajectory_validator_inside():
    from drivers.xarm_driver.safety import TrajectoryValidator
    def get_fk(joints):
        # Fake FK: midpoint of trajectory
        x = 50 + (joints[0] / 10.0)
        return (0, [x, 0, 600])
    validator = TrajectoryValidator(num_points=5)
    r = validator.validate_trajectory([0, 0, 0, 0, 0, 0], [10, 0, 0, 0, 0, 0], get_fk)
    assert r.ok is True


def test_trajectory_validator_outside():
    from drivers.xarm_driver.safety import TrajectoryValidator
    def get_fk(joints):
        return (0, [300, 0, 600])  # x=300 outside box
    validator = TrajectoryValidator(num_points=5)
    r = validator.validate_trajectory([0, 0, 0, 0, 0, 0], [10, 0, 0, 0, 0, 0], get_fk)
    assert r.ok is False
