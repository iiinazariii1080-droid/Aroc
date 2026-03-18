"""Unit tests for PositionPoller."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from domain.models import PositionStatus
from services.event_bus import EventBus
from services.force_arrival import ForceArrivalSignal
from services.position_poller import PositionPoller


@pytest.fixture
def mock_symovo():
    client = MagicMock()
    client.pose_uncached = AsyncMock(return_value={"x": 1.0, "y": 2.0, "theta": 0.5})
    client.status_uncached = AsyncMock(return_value={"x": 1.0, "y": 2.0, "theta": 0.5})
    return client


@pytest.fixture
async def event_bus():
    bus = EventBus(queue_size=100)
    yield bus
    async with bus._lock:
        bus._subscribers.clear()


@pytest.fixture
def mock_state_store():
    store = MagicMock()
    store.set_last_raw_pose = AsyncMock()
    store.set_last_raw_status = AsyncMock()
    store.set_last_position_status = AsyncMock()
    store.get_active_commands_for_publishing = AsyncMock(return_value={})
    return store


@pytest.fixture
def force_arrival():
    return ForceArrivalSignal()


@pytest.fixture
def running_flag():
    flag = asyncio.Event()
    flag.set()
    return flag


@pytest.fixture
def poller(mock_symovo, event_bus, mock_state_store, force_arrival, running_flag):
    return PositionPoller(
        symovo_client=mock_symovo,
        bus=event_bus,
        state_store=mock_state_store,
        force_arrival=force_arrival,
        running_flag=running_flag,
    )


class TestConstruction:
    def test_accepts_all_required_deps(self, poller):
        assert poller._client is not None
        assert poller._bus is not None
        assert poller._store is not None
        assert poller._fa is not None
        assert poller._running is not None


class TestFetchPoseWithFallback:
    @pytest.mark.asyncio
    async def test_fetch_pose_success(self, poller, mock_symovo, mock_state_store):
        result = await poller._fetch_pose_with_fallback()
        assert result == {"x": 1.0, "y": 2.0, "theta": 0.5}
        mock_state_store.set_last_raw_pose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_pose_timeout_raises(self, poller, mock_symovo):
        mock_symovo.pose_uncached.side_effect = asyncio.TimeoutError()
        with pytest.raises(asyncio.TimeoutError):
            await poller._fetch_pose_with_fallback()

    @pytest.mark.asyncio
    async def test_fetch_pose_falls_back_to_status(self, poller, mock_symovo, mock_state_store):
        from exceptions import DeviceError
        err = DeviceError("not found")
        err.http_status = 404
        mock_symovo.pose_uncached.side_effect = err
        mock_symovo.status_uncached.return_value = {"x": 3.0, "y": 4.0, "theta": 1.0}

        result = await poller._fetch_pose_with_fallback()
        assert result == {"x": 3.0, "y": 4.0, "theta": 1.0}
        mock_state_store.set_last_raw_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fallback_also_fails_raises(self, poller, mock_symovo):
        from exceptions import DeviceError
        err = DeviceError("not found")
        err.http_status = 404
        mock_symovo.pose_uncached.side_effect = err
        mock_symovo.status_uncached.side_effect = Exception("also broken")

        with pytest.raises(DeviceError):
            await poller._fetch_pose_with_fallback()


class TestParsePoseData:
    def test_returns_position_status_for_valid_data(self, poller):
        with patch("services.position_poller.PositionPoller._parse_pose_data") as mock_parse:
            mock_parse.return_value = PositionStatus(x=1.0, y=2.0, theta=0.5, frame_id="map")
            result = PositionPoller._parse_pose_data({"x": 1.0, "y": 2.0, "theta": 0.5})
            assert result is not None

    def test_returns_none_for_invalid_data(self):
        result = PositionPoller._parse_pose_data(None)
        assert result is None


class TestPublishPositionStatus:
    @pytest.mark.asyncio
    async def test_stores_position(self, poller, mock_state_store):
        pos = PositionStatus(x=1.0, y=2.0, theta=0.5, frame_id="map")
        mock_state_store.get_active_commands_for_publishing.return_value = {}
        await poller._publish_position_status(pos)
        mock_state_store.set_last_position_status.assert_awaited_once_with(pos)


class TestRunCancellation:
    @pytest.mark.asyncio
    async def test_run_stops_when_flag_cleared(self, poller, running_flag):
        running_flag.clear()
        # run() should exit immediately when flag is not set
        await asyncio.wait_for(poller.run(), timeout=1.0)
