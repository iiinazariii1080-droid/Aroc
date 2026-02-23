from drivers.dryve_d1.protocol.gateway_telegram import build_read_adu, build_write_adu, parse_adu
from drivers.dryve_d1.protocol.validator import validate_gateway_request, extract_index_subindex


def test_build_read_adu_is_exact_19_bytes():
    telegram = build_read_adu(transaction_id=1, unit_id=1, index=0x6041, subindex=0, byte_count=2)
    adu = telegram.adu
    validate_gateway_request(adu, expect_write=False)
    assert len(adu) == 19
    # Verify structure manually (parse_adu is for responses, not requests)
    assert adu[7] == 0x2B  # function code
    assert adu[9] == 0x00  # protocol control (read)
    index, subindex = extract_index_subindex(adu)
    assert index == 0x6041
    assert subindex == 0
    assert adu[18] == 2  # byte_count


def test_build_write_adu_includes_data_and_length_matches():
    telegram = build_write_adu(transaction_id=7, unit_id=1, index=0x6040, subindex=0, data=b"\x0f\x00")
    adu = telegram.adu
    validate_gateway_request(adu, expect_write=True)
    assert len(adu) == 21  # 19 + byte_count(2)
    req = parse_adu(adu)
    assert req.protocol_control == 1
    assert req.byte_count == 2
    assert req.data == b"\x0f\x00"
