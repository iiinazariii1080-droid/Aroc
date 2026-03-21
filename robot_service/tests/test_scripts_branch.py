"""Extra tests for robot_scripts.py — branch coverage targets."""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.robot_scripts import _ns_to_dict, get_robot_system_status


# ── _ns_to_dict branch coverage ─────────────────────────────────
def test_ns_to_dict_pydantic_v1_dict():
    """Object with dict() but no model_dump() → obj.dict() fallback."""

    class V1Model:
        def dict(self):
            return {"key": "val"}

    obj = V1Model()
    assert _ns_to_dict(obj) == {"key": "val"}


def test_ns_to_dict_list():
    """Lists are recursed."""
    ns = SimpleNamespace(x=1)
    result = _ns_to_dict([ns, "plain"])
    assert result == [{"x": 1}, "plain"]


def test_ns_to_dict_dict_values():
    """Dict values are recursed."""
    ns = SimpleNamespace(a=2)
    result = _ns_to_dict({"nested": ns, "flat": 5})
    assert result == {"nested": {"a": 2}, "flat": 5}


def test_ns_to_dict_exception_fallback():
    """When conversion fails, falls back to str(obj)."""

    class Broken:
        def model_dump(self):
            raise RuntimeError("oops")

        def __str__(self):
            return "broken"

    assert _ns_to_dict(Broken()) == "broken"


# ── get_robot_system_status: ErrorStatus results ────────────────
@pytest.mark.asyncio
async def test_system_status_all_error():
    """When gather returns exceptions, they become ErrorStatus."""
    from models.api_types import ErrorStatus

    with (
        patch("app.robot_scripts.lift") as mock_lift,
        patch("app.robot_scripts.agv") as mock_agv,
    ):
        mock_lift.status = AsyncMock(side_effect=ConnectionError("igus offline"))
        mock_agv.status = AsyncMock(side_effect=TimeoutError("symovo timeout"))

        with patch("app.robot_scripts.xarm_status") as x_status:
            x_status.get_status.return_value = None
            x_status.get_last_updated_ts.return_value = 0

            result = await get_robot_system_status()

    assert result["ready"] is False
    assert isinstance(result["igus"], ErrorStatus)
    assert isinstance(result["symovo"], ErrorStatus)
