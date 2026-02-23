import asyncio
import pytest

from message_router import MessageRouter


@pytest.mark.asyncio
async def test_router_wraps_list_and_calls_on_report_sync():
    captured = {}

    def on_report_sync(msg):
        captured["msg"] = msg

    router = MessageRouter(
        on_report=on_report_sync,
        on_response=None,
        on_request=None,
        on_invalid=None,
    )

    await router.route(["a", "b"])  # should wrap into report with data=list

    assert "msg" in captured
    msg = captured["msg"]
    assert msg["type"] == "report"
    assert msg["cmd"] == "devices_status_report"
    assert isinstance(msg["data"], list)


@pytest.mark.asyncio
async def test_router_calls_async_invalid_on_bad_type():
    called = asyncio.Event()

    async def on_invalid_async(info):
        called.set()

    router = MessageRouter(on_invalid=on_invalid_async)
    await router.route({"type": "weird", "cmd": "x"})

    assert called.is_set()


