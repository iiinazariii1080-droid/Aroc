import pytest

from drivers.dryve_d1.protocol.codec import pack_int, unpack_int


def test_pack_unpack_u16_le_roundtrip():
    b = pack_int(65535, size=2, signed=False, endian="<")
    assert b == b"\xff\xff"
    assert unpack_int(b, signed=False, endian="<") == 65535


def test_pack_unpack_i16_le_roundtrip():
    b = pack_int(-2, size=2, signed=True, endian="<")
    assert b == b"\xfe\xff"
    assert unpack_int(b, signed=True, endian="<") == -2


def test_pack_range_error_unsigned():
    with pytest.raises(ValueError):
        pack_int(-1, size=2, signed=False, endian="<")
    with pytest.raises(ValueError):
        pack_int(1 << 16, size=2, signed=False, endian="<")


def test_pack_range_error_signed():
    with pytest.raises(ValueError):
        pack_int(-(1 << 15) - 1, size=2, signed=True, endian="<")
    with pytest.raises(ValueError):
        pack_int((1 << 15), size=2, signed=True, endian="<")
