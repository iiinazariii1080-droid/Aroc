from __future__ import annotations

from dataclasses import dataclass

from .codec import pack_int, unpack_int
from .exceptions import ResponseMismatch
from .gateway_telegram import (
    GatewayResponse,
    GatewayTelegram,
    build_read_adu,
    build_write_adu,
    parse_adu,
)


@dataclass(frozen=True, slots=True)
class SDOReadRequest:
    index: int
    subindex: int = 0
    byte_count: int = 2


@dataclass(frozen=True, slots=True)
class SDOWriteRequest:
    index: int
    subindex: int = 0
    data: bytes = b""


class SDOClient:
    """Pure serialization client for dryve D1 Modbus TCP Gateway SDO access.

    This class DOES NOT perform networking. Instead it:
    - builds strict read/write ADUs
    - parses/validates responses
    - converts integers to/from little-endian data field as required by the manual

    Typical usage (with your own transport layer):
        req = client.build_read(SDOReadRequest(0x6041, 0, 2))
        resp_bytes = transport.send_and_recv(req.adu)
        value_bytes = client.parse_read_response(resp_bytes, request=req)
    """

    def __init__(self, *, unit_id: int = 0) -> None:
        if not (0 <= unit_id <= 0xFF):
            raise ValueError("unit_id must be 0..255")
        self._unit_id = unit_id

    @property
    def unit_id(self) -> int:
        return self._unit_id

    # -----------------------
    # Build telegrams (ADU)
    # -----------------------
    def build_read(self, req: SDOReadRequest, *, transaction_id: int) -> GatewayTelegram:
        return build_read_adu(
            transaction_id=transaction_id,
            unit_id=self._unit_id,
            index=req.index,
            subindex=req.subindex,
            byte_count=req.byte_count,
        )

    def build_write(self, req: SDOWriteRequest, *, transaction_id: int) -> GatewayTelegram:
        return build_write_adu(
            transaction_id=transaction_id,
            unit_id=self._unit_id,
            index=req.index,
            subindex=req.subindex,
            data=req.data,
        )

    # -----------------------
    # Parse responses
    # -----------------------
    def parse_response(self, adu: bytes, *, request: GatewayTelegram | None = None) -> GatewayResponse:
        return parse_adu(adu, request=request)

    def parse_read_response(self, adu: bytes, *, request: GatewayTelegram) -> bytes:
        resp = self.parse_response(adu, request=request)
        if resp.protocol_control != 0:
            raise ResponseMismatch("Expected read response (protocol_control=0)")
        if resp.byte_count is None:
            raise ResponseMismatch("Missing byte_count in read response")
        if len(resp.data) != resp.byte_count:
            raise ResponseMismatch(f"Read response data length mismatch: got {len(resp.data)}, expected {resp.byte_count}")
        return resp.data

    def parse_write_response(self, adu: bytes, *, request: GatewayTelegram) -> None:
        resp = self.parse_response(adu, request=request)
        # Some devices return protocol_control=0 for write responses (echoing request format)
        # Accept both 0 and 1 as valid write responses
        if resp.protocol_control not in (0, 1):
            raise ResponseMismatch(f"Expected write response (protocol_control=0 or 1), got {resp.protocol_control}")
        # If drive returns an ack without data it will have byte_count=0 and no data
        if resp.byte_count is None:
            raise ResponseMismatch("Missing byte_count in write response")
        if resp.byte_count == 0:
            if resp.data:
                raise ResponseMismatch("Write ack has byte_count=0 but still contains data")
            return
        # Tolerant: some devices echo written data
        if len(resp.data) != resp.byte_count:
            raise ResponseMismatch(f"Write response data length mismatch: got {len(resp.data)}, expected {resp.byte_count}")

    # -----------------------
    # Convenience value helpers
    # -----------------------
    def build_read_int(self, *, index: int, subindex: int, size: int, signed: bool, transaction_id: int) -> GatewayTelegram:
        return self.build_read(SDOReadRequest(index=index, subindex=subindex, byte_count=size), transaction_id=transaction_id)

    def decode_read_int(self, adu: bytes, *, request: GatewayTelegram, signed: bool) -> int:
        data = self.parse_read_response(adu, request=request)
        return unpack_int(data, signed=signed, endian="<")  # data field is little-endian

    def build_write_int(self, *, index: int, subindex: int, value: int, size: int, signed: bool, transaction_id: int) -> GatewayTelegram:
        data = pack_int(value, size=size, signed=signed, endian="<")  # data field is little-endian
        return self.build_write(SDOWriteRequest(index=index, subindex=subindex, data=data), transaction_id=transaction_id)
