"""
Unit tests for domain models.
"""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from domain.models import (
    ActiveTransport,
    NavigationCommand,
    NavigationSession,
    NavigationStatus,
    NavigationStatusEnum,
    Pose2D,
    PositionStatus,
    TargetConfig,
)


# -- NavigationCommand ---------------------------------------------------------


class TestNavigationCommand:
    def test_minimal_fields(self):
        cmd = NavigationCommand(
            command_id="abc-123",
            timestamp="2025-01-01T00:00:00Z",
        )
        assert cmd.command_id == "abc-123"
        assert cmd.x is None
        assert cmd.y is None
        assert cmd.theta == 0.0
        assert cmd.map_id == 0
        assert cmd.station_id is None
        assert cmd.target_id == ""
        assert cmd.max_speed_m_s is None

    def test_all_fields(self):
        cmd = NavigationCommand(
            command_id="abc-123",
            timestamp="2025-01-01T00:00:00Z",
            target_id="kitchen",
            x=1.5,
            y=2.5,
            theta=3.14,
            map_id=2,
            station_id=10,
            max_speed_m_s=0.5,
        )
        assert cmd.target_id == "kitchen"
        assert cmd.x == 1.5
        assert cmd.y == 2.5
        assert cmd.theta == 3.14
        assert cmd.map_id == 2
        assert cmd.station_id == 10
        assert cmd.max_speed_m_s == 0.5


# -- NavigationStatus ----------------------------------------------------------


class TestNavigationStatus:
    def test_creation(self):
        status = NavigationStatus(
            status=NavigationStatusEnum.NAVIGATING,
            goal_id="cmd-1",
            progress_percent=50,
        )
        assert status.status == NavigationStatusEnum.NAVIGATING
        assert status.progress_percent == 50

    def test_progress_lower_bound(self):
        with pytest.raises(ValidationError):
            NavigationStatus(
                status=NavigationStatusEnum.IDLE,
                progress_percent=-1,
            )

    def test_progress_upper_bound(self):
        with pytest.raises(ValidationError):
            NavigationStatus(
                status=NavigationStatusEnum.IDLE,
                progress_percent=101,
            )

    def test_progress_at_boundaries(self):
        s0 = NavigationStatus(status=NavigationStatusEnum.IDLE, progress_percent=0)
        assert s0.progress_percent == 0

        s100 = NavigationStatus(status=NavigationStatusEnum.ARRIVED, progress_percent=100)
        assert s100.progress_percent == 100


# -- PositionStatus ------------------------------------------------------------


class TestPositionStatus:
    def test_creation(self):
        pos = PositionStatus(x=1.0, y=2.0, theta=0.5)
        assert pos.x == 1.0
        assert pos.y == 2.0
        assert pos.theta == 0.5
        assert pos.frame_id == "map"

    def test_custom_frame_id(self):
        pos = PositionStatus(x=0.0, y=0.0, theta=0.0, frame_id="odom")
        assert pos.frame_id == "odom"


# -- ActiveTransport -----------------------------------------------------------


class TestActiveTransport:
    def test_creation_with_defaults(self):
        t = ActiveTransport(
            command_id="cmd-1",
            transport_id="t-1",
            state=1,
        )
        assert t.command_id == "cmd-1"
        assert t.transport_id == "t-1"
        assert t.state == 1
        assert t.target_id is None
        assert t.generation == 0
        assert isinstance(t.created_at, datetime)

    def test_default_timestamp_is_utc(self):
        t = ActiveTransport(command_id="c", transport_id="t", state=0)
        assert t.created_at.tzinfo is not None


# -- Pose2D --------------------------------------------------------------------


class TestPose2D:
    def test_creation(self):
        p = Pose2D(x=3.0, y=4.0)
        assert p.x == 3.0
        assert p.y == 4.0
        assert p.map_id is None

    def test_with_map_id(self):
        p = Pose2D(x=0.0, y=0.0, map_id=5)
        assert p.map_id == 5


# -- NavigationSession ---------------------------------------------------------


class TestNavigationSession:
    def test_creation(self):
        session = NavigationSession(
            command_id="cmd-1",
            target_id="pos_A",
            start=Pose2D(x=0.0, y=0.0),
            goal=Pose2D(x=10.0, y=10.0),
            total_dist_m=14.14,
            min_remaining_dist_m=14.14,
            progress_percent=0,
        )
        assert session.command_id == "cmd-1"
        assert session.target_id == "pos_A"
        assert session.total_dist_m == 14.14
        assert isinstance(session.created_at, datetime)
        assert isinstance(session.updated_at, datetime)

    def test_progress_bounds(self):
        with pytest.raises(ValidationError):
            NavigationSession(
                command_id="c",
                start=Pose2D(x=0, y=0),
                goal=Pose2D(x=1, y=1),
                total_dist_m=1.0,
                min_remaining_dist_m=1.0,
                progress_percent=101,
            )


# -- TargetConfig --------------------------------------------------------------


class TestTargetConfig:
    def test_pose_based(self):
        t = TargetConfig(x=1.0, y=2.0, theta=0.5)
        assert t.is_station is False

    def test_station_based(self):
        t = TargetConfig(station_id=42)
        assert t.is_station is True

    def test_both_pose_and_station_raises(self):
        with pytest.raises(ValidationError, match="not both"):
            TargetConfig(x=1.0, y=2.0, station_id=42)

    def test_neither_pose_nor_station_raises(self):
        with pytest.raises(ValidationError, match="must specify"):
            TargetConfig()
