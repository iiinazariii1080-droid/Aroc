from __future__ import annotations

from drivers.dryve_d1.motion.profile_position import ProfilePosition, ProfilePositionConfig
from drivers.dryve_d1.od.controlword import CWBit
from drivers.dryve_d1.od.indices import ODIndex


def _bit(word: int, bit: int) -> bool:
    return bool((int(word) >> int(bit)) & 1)


class _FakeOD:
    def __init__(self, *, statuswords: list[int] | None = None) -> None:
        self.statuswords = list(statuswords or [])
        self.writes_u8: list[tuple[int, int, int]] = []
        self.writes_u16: list[tuple[int, int, int]] = []
        self.writes_u32: list[tuple[int, int, int]] = []
        self.writes_i32: list[tuple[int, int, int]] = []
        self.read_i32_value = 0

    async def read_u16(self, index: int, subindex: int = 0) -> int:
        if self.statuswords:
            return self.statuswords.pop(0)
        return 0

    async def read_i8(self, index: int, subindex: int = 0) -> int:
        return 1

    async def read_i32(self, index: int, subindex: int = 0) -> int:
        return int(self.read_i32_value)

    async def write_u16(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes_u16.append((index, value, subindex))

    async def write_u8(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes_u8.append((index, value, subindex))

    async def write_u32(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes_u32.append((index, value, subindex))

    async def write_i32(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes_i32.append((index, value, subindex))


async def test_ensure_mode_always_writes_mode_register() -> None:
    od = _FakeOD()
    pp = ProfilePosition(
        od,
        config=ProfilePositionConfig(
            verify_mode=False,
            mode_settle_s=0.0,
        ),
    )

    await pp.ensure_mode()

    assert (int(ODIndex.MODES_OF_OPERATION), 1, 0) in od.writes_u8


async def test_move_to_pulses_new_setpoint_set_then_clear() -> None:
    sw_target_reached = 1 << 10
    od = _FakeOD(
        statuswords=[
            0,  # barrier read before start pulse
            0,  # ack read: target_reached == 0 -> ack seen
            sw_target_reached,  # wait_target_reached: done
        ]
    )
    pp = ProfilePosition(
        od,
        config=ProfilePositionConfig(
            verify_mode=False,
            mode_settle_s=0.0,
            system_cycle_delay_s=0.001,
            poll_interval_s=0.0,
            move_timeout_s=0.5,
        ),
    )

    await pp.move_to(target_position=12345, timeout_s=0.5)

    cw_writes = [w for w in od.writes_u16 if w[0] == int(ODIndex.CONTROLWORD)]
    assert len(cw_writes) >= 2

    set_word = cw_writes[-2][1]
    clear_word = cw_writes[-1][1]

    assert _bit(set_word, int(CWBit.NEW_SET_POINT))
    assert not _bit(clear_word, int(CWBit.NEW_SET_POINT))
