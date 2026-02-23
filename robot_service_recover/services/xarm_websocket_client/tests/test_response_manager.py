import asyncio
import pytest

from response_manager import ResponseManager


@pytest.mark.asyncio
async def test_response_manager_resolve_and_timeout():
    rm = ResponseManager(default_timeout=0.1)

    fut = rm.create_waiter("42")
    # resolve before timeout
    await rm.resolve({"type": "response", "id": "42", "code": 0})
    res = await asyncio.wait_for(fut, timeout=0.2)
    assert res["id"] == "42"

    # create another waiter that will timeout
    fut2 = rm.create_waiter("43", timeout=0.05)
    with pytest.raises(asyncio.TimeoutError):
        await fut2
    rm.cancel_all()


