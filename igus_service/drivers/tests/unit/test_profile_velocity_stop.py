import pytest

from drivers.dryve_d1.motion.profile_velocity import ProfileVelocity, ProfileVelocityConfig


class FakeOD:
    def __init__(self):
        self.writes = []

    async def write_i32(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes.append((index, value, subindex))

    async def write_u8(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes.append((index, value, subindex))

    async def write_u32(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes.append((index, value, subindex))

    async def write_u16(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes.append((index, value, subindex))

    async def read_u16(self, index: int, subindex: int = 0) -> int:
        return 0


@pytest.mark.asyncio
async def test_stop_velocity_zero_writes_target_velocity_zero():
    od = FakeOD()
    pv = ProfileVelocity(od, config=ProfileVelocityConfig(verify_mode=False))
    await pv.stop_velocity_zero()
    assert (0x60FF, 0, 0) in od.writes
