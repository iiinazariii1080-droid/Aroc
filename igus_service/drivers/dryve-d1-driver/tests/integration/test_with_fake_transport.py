import asyncio
import pytest

from drivers.dryve_d1.api.drive import DryveD1, DryveD1Config
from drivers.dryve_d1.config.models import DriveConfig, ConnectionConfig
from drivers.dryve_d1.protocol.validator import validate_gateway_request, validate_gateway_response, parse_mbap, extract_index_subindex


def _make_read_response(request_adu: bytes, data: bytes) -> bytes:
    mbap = parse_mbap(request_adu)
    index, subindex = extract_index_subindex(request_adu)
    bc = len(data)
    # Build PDU for read response
    pdu = bytes([
        0x2B, 0x0D, 0x00, 0x00, 0x00,
        (index >> 8) & 0xFF, index & 0xFF, subindex & 0xFF,
        0x00, 0x00, 0x00,
        bc & 0xFF,
    ]) + data
    length = len(pdu) + 1
    mbap_bytes = mbap.transaction_id.to_bytes(2, "big") + (0).to_bytes(2, "big") + length.to_bytes(2, "big") + bytes([mbap.unit_id])
    return mbap_bytes + pdu


def _make_write_ack(request_adu: bytes) -> bytes:
    mbap = parse_mbap(request_adu)
    index, subindex = extract_index_subindex(request_adu)
    # byte_count = 0 ACK (19 bytes total)
    pdu = bytes([
        0x2B, 0x0D, 0x01, 0x00, 0x00,
        (index >> 8) & 0xFF, index & 0xFF, subindex & 0xFF,
        0x00, 0x00, 0x00,
        0x00,  # byte_count
    ])
    length = len(pdu) + 1
    mbap_bytes = mbap.transaction_id.to_bytes(2, "big") + (0).to_bytes(2, "big") + length.to_bytes(2, "big") + bytes([mbap.unit_id])
    return mbap_bytes + pdu


class FakeSession:
    def __init__(self):
        self._tid = 0
        self.od = {
            (0x6041, 0): (0x0027).to_bytes(2, "little"),  # Statusword: operation enabled pattern
            (0x6064, 0): (12345).to_bytes(4, "little", signed=True),  # Position actual
        }

    def next_transaction_id(self) -> int:
        self._tid = (self._tid + 1) & 0xFFFF
        return self._tid

    def is_connected(self) -> bool:
        return True

    def transceive(self, adu: bytes, *, deadline_s: float) -> bytes:
        validate_gateway_request(adu)
        mbap = parse_mbap(adu)
        index, subindex = extract_index_subindex(adu)
        protocol_control = adu[9]  # 0 = read, 1 = write
        byte_count = adu[18]
        
        if protocol_control == 0:
            # read request
            data = self.od.get((index, subindex), bytes([0] * byte_count))
            # ensure we return exactly requested byte_count (pad/truncate)
            data = (data + bytes([0] * byte_count))[: byte_count]
            resp = _make_read_response(adu, data)
            validate_gateway_response(resp)
            return resp
        else:
            # write request: extract data and store
            data = adu[19:19+byte_count] if len(adu) > 19 else b""
            self.od[(index, subindex)] = data
            resp = _make_write_ack(adu)
            validate_gateway_response(resp)
            return resp


@pytest.mark.asyncio
async def test_drive_read_write_with_fake_session():
    connection = ConnectionConfig(host="127.0.0.1", unit_id=1)
    drive_config = DriveConfig(connection=connection)
    cfg = DryveD1Config(drive=drive_config)
    d = DryveD1(config=cfg)
    # Inject fake session and bypass connect()
    d._session = FakeSession()  # type: ignore[attr-defined]

    sw = await d.read_u16(0x6041, 0)
    assert sw == 0x0027

    await d.write_u16(0x6040, 0x000F, 0)
    # read it back
    cw = await d.read_u16(0x6040, 0)
    assert cw == 0x000F
