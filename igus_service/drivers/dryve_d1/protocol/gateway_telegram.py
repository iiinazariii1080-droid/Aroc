from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .exceptions import (
    ModbusGatewayException,
    ResponseMismatch,
)
from .validator import (
    GATEWAY_EXCEPTION_MASK,
    GATEWAY_FUNCTION_CODE,
    GATEWAY_MEI_TYPE,
    extract_index_subindex,
    parse_mbap,
    validate_gateway_request,
    validate_gateway_response,
)

_WARN_INTERVAL_S = 10.0
_last_warned: dict[str, float] = {}
_warn_lock = threading.Lock()


def _rate_limited_warning(logger: logging.Logger, key: str, msg: str, *args: Any) -> None:
    """Log a WARNING at most once per _WARN_INTERVAL_S per key (thread-safe)."""
    now = time.monotonic()
    with _warn_lock:
        if now - _last_warned.get(key, 0.0) >= _WARN_INTERVAL_S:
            _last_warned[key] = now
            logger.warning(msg, *args)


@dataclass(frozen=True, slots=True)
class GatewayTelegram:
    """A strict Modbus TCP Gateway telegram (ADU) for dryve D1."""

    adu: bytes
    _tid: int = field(default=0, init=False, repr=False)
    _uid: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        mbap = parse_mbap(self.adu)
        object.__setattr__(self, "_tid", mbap.transaction_id)
        object.__setattr__(self, "_uid", mbap.unit_id)

    @property
    def transaction_id(self) -> int:
        return self._tid

    @property
    def unit_id(self) -> int:
        return self._uid


@dataclass(frozen=True, slots=True)
class GatewayResponse:
    """Parsed gateway response."""

    transaction_id: int
    unit_id: int
    function_code: int
    protocol_control: int | None = None  # 0 read, 1 write
    index: int | None = None
    subindex: int | None = None
    byte_count: int | None = None
    data: bytes = b""
    exception_code: int | None = None

    @property
    def is_exception(self) -> bool:
        return self.exception_code is not None


def _mbap_bytes(transaction_id: int, *, length: int, unit_id: int) -> bytes:
    if not (0 <= transaction_id <= 0xFFFF):
        raise ValueError("transaction_id must be 0..65535")
    if not (0 <= length <= 0xFFFF):
        raise ValueError("length must be 0..65535")
    if not (0 <= unit_id <= 0xFF):
        raise ValueError("unit_id must be 0..255")

    return bytes([
        (transaction_id >> 8) & 0xFF,
        transaction_id & 0xFF,
        0x00, 0x00,  # protocol id
        (length >> 8) & 0xFF,
        length & 0xFF,
        unit_id & 0xFF,
    ])


def build_read_adu(
    *,
    transaction_id: int,
    unit_id: int,
    index: int,
    subindex: int = 0,
    byte_count: int = 2,
) -> GatewayTelegram:
    """Build a strict READ ADU.

    Manual constraint:
    - MBAP length (Byte 5) MUST be 0x0D for reads (13 bytes after Byte 5)
    - Telegram MUST stop after Byte 18 (no data bytes in request)
    """
    if byte_count not in (1, 2, 3, 4):
        raise ValueError("byte_count must be 1..4")
    if not (0 <= index <= 0xFFFF):
        raise ValueError("index must be 0..65535")
    if not (0 <= subindex <= 0xFF):
        raise ValueError("subindex must be 0..255")

    # PDU is 12 bytes for gateway read/write headers through Byte 18
    pdu = bytes([
        GATEWAY_FUNCTION_CODE,     # Byte 7
        GATEWAY_MEI_TYPE,          # Byte 8
        0x00,                      # Byte 9: read
        0x00,                      # Byte 10: reserved
        0x00,                      # Byte 11: node id (not used)
        (index >> 8) & 0xFF,       # Byte 12: index MSB
        index & 0xFF,              # Byte 13: index LSB
        subindex & 0xFF,           # Byte 14
        0x00, 0x00, 0x00,          # Byte 15..17
        byte_count & 0xFF,         # Byte 18
    ])

    length = 1 + len(pdu)  # unit_id + pdu
    adu = _mbap_bytes(transaction_id, length=length, unit_id=unit_id) + pdu
    validate_gateway_request(adu, expect_write=False)
    return GatewayTelegram(adu=adu)


def build_write_adu(
    *,
    transaction_id: int,
    unit_id: int,
    index: int,
    subindex: int = 0,
    data: bytes,
) -> GatewayTelegram:
    """Build a strict WRITE ADU.

    Manual constraint:
    - MBAP length (Byte 5) MUST be 0x0D + N for writes (N = data length 1..4)
    - Data bytes MUST be present (Byte 19..)
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data must be bytes")
    data = bytes(data)
    if len(data) not in (1, 2, 3, 4):
        raise ValueError("data length must be 1..4")
    if not (0 <= index <= 0xFFFF):
        raise ValueError("index must be 0..65535")
    if not (0 <= subindex <= 0xFF):
        raise ValueError("subindex must be 0..255")

    pdu = bytes([
        GATEWAY_FUNCTION_CODE,     # Byte 7
        GATEWAY_MEI_TYPE,          # Byte 8
        0x01,                      # Byte 9: write
        0x00,                      # Byte 10: reserved
        0x00,                      # Byte 11: node id (not used)
        (index >> 8) & 0xFF,       # Byte 12: index MSB
        index & 0xFF,              # Byte 13: index LSB
        subindex & 0xFF,           # Byte 14
        0x00, 0x00, 0x00,          # Byte 15..17
        len(data) & 0xFF,          # Byte 18: byte count
    ]) + data

    length = 1 + len(pdu)  # unit_id + pdu
    adu = _mbap_bytes(transaction_id, length=length, unit_id=unit_id) + pdu
    validate_gateway_request(adu, expect_write=True)
    return GatewayTelegram(adu=adu)


def parse_adu(
    adu: bytes,
    *,
    request: GatewayTelegram | None = None,
    tid_mismatch_ok: bool | None = None,
    unit_id_wildcard_ok: bool | None = None,
) -> GatewayResponse:
    """Parse a received ADU, validate it, and (optionally) match it to a request.

    Args:
        tid_mismatch_ok: Tolerate TID mismatch. ``None`` → consult runtime_policy.
        unit_id_wildcard_ok: Tolerate unit-id wildcard. ``None`` → consult runtime_policy.
    """
    if not isinstance(adu, (bytes, bytearray)):
        raise TypeError("adu must be bytes")
    adu = bytes(adu)

    validate_gateway_response(adu)
    mbap = parse_mbap(adu)

    func = adu[7]
    if func == (GATEWAY_FUNCTION_CODE | GATEWAY_EXCEPTION_MASK):
        exc_code = adu[8]
        raise ModbusGatewayException(
            function_code=func,
            exception_code=exc_code,
            transaction_id=mbap.transaction_id,
            unit_id=mbap.unit_id,
        )

    # Normal response
    proto_ctrl = adu[9]
    index, sub = extract_index_subindex(adu)
    bc = adu[18]
    data = b""
    if len(adu) > 19:
        data = adu[19:]

    resp = GatewayResponse(
        transaction_id=mbap.transaction_id,
        unit_id=mbap.unit_id,
        function_code=func,
        protocol_control=proto_ctrl,
        index=index,
        subindex=sub,
        byte_count=bc,
        data=data,
        exception_code=None,
    )

    if request is not None:
        # Some devices don't properly echo transaction IDs
        # Since we're in a serialized session (one request at a time), we can validate
        # by unit_id, index, and subindex instead of transaction_id
        if resp.unit_id != request.unit_id:
            # NOTE: Many Modbus TCP gateways / simulators respond with Unit Identifier 0x00 (or 0xFF)
            # regardless of the requested Unit ID. In Modbus TCP, the Unit ID is primarily used for
            # bridging to serial lines; for direct TCP devices it may be ignored.
            # We treat 0x00 and 0xFF as "wildcard" Unit IDs by default for compatibility with simulators,
            # while keeping strict validation for other mismatches.
            if resp.unit_id in (0x00, 0xFF):
                if unit_id_wildcard_ok is not None:
                    allow = unit_id_wildcard_ok
                else:
                    from ..config.runtime_policy import allow_unit_id_wildcard
                    allow = allow_unit_id_wildcard()
                if allow:
                    _rate_limited_warning(
                        logging.getLogger(__name__),
                        "unit_id_mismatch",
                        "Unit ID mismatch tolerated (wildcard): resp=%d, req=%d (index=%04X:%d). "
                        "Set DRYVE_ALLOW_UNIT_ID_WILDCARD=0 to enforce strict Unit ID checking.",
                        resp.unit_id,
                        request.unit_id,
                        resp.index if resp.index is not None else 0,
                        resp.subindex if resp.subindex is not None else 0,
                    )
                else:
                    raise ResponseMismatch(f"Unit ID mismatch: resp={resp.unit_id}, req={request.unit_id}")
            else:
                raise ResponseMismatch(f"Unit ID mismatch: resp={resp.unit_id}, req={request.unit_id}")
        # For request matching we also ensure the index/subindex match for normal responses
        req_index, req_sub = extract_index_subindex(request.adu)
        if resp.index != req_index or resp.subindex != req_sub:
            raise ResponseMismatch(f"Index/subindex mismatch: resp={resp.index:04X}:{resp.subindex}, req={req_index:04X}:{req_sub}")
        # Transaction ID validation: strict by default (per dryve D1 spec)
        # Mismatch indicates protocol error or race condition - should not be ignored
        if resp.transaction_id != request.transaction_id:
            # Allow opt-in relaxation for simulators/devices that don't echo TID.
            if tid_mismatch_ok is not None:
                allow = tid_mismatch_ok
            else:
                from ..config.runtime_policy import allow_tid_mismatch
                allow = allow_tid_mismatch()
            if allow:
                _rate_limited_warning(
                    logging.getLogger(__name__),
                    "tid_mismatch",
                    "Transaction ID mismatch tolerated: resp=%d, req=%d (index=%04X:%d). "
                    "Set DRYVE_ALLOW_TID_MISMATCH=0 to re-enable strict checking.",
                    resp.transaction_id,
                    request.transaction_id,
                    resp.index,
                    resp.subindex,
                )
            else:
                raise ResponseMismatch(
                    f"Transaction ID mismatch: resp={resp.transaction_id}, req={request.transaction_id}. "
                    "This indicates a protocol error or race condition. "
                    f"Response index={resp.index:04X}:{resp.subindex}, request index={req_index:04X}:{req_sub}"
                )
        # Read requests expect data length = byte_count; write requests typically ack with byte_count=0.
        req_is_write = request.adu[9] == 0x01
        if not req_is_write:
            resp_byte_count = int(resp.byte_count) if resp.byte_count is not None else 0
            req_byte_count = request.adu[18]
            if len(resp.data) != resp_byte_count:
                raise ResponseMismatch(f"Data length mismatch in read response: got {len(resp.data)}, expected {resp_byte_count}")
            # Tolerate devices that return fewer bytes than requested:
            # the dryve D1 gateway may return 2 bytes for a 4-byte read when
            # the OD object is 16-bit.  Zero-pad (little-endian) so the
            # caller always sees the requested width.
            if resp_byte_count < req_byte_count:
                _rate_limited_warning(
                    logging.getLogger(__name__),
                    "byte_count_short",
                    "Read response shorter than requested: resp=%d, req=%d (index=%04X:%d). "
                    "Zero-padding to requested size.",
                    resp_byte_count,
                    req_byte_count,
                    resp.index if resp.index is not None else 0,
                    resp.subindex if resp.subindex is not None else 0,
                )
                padded_data = resp.data + b"\x00" * (req_byte_count - resp_byte_count)
                resp = GatewayResponse(
                    transaction_id=resp.transaction_id,
                    unit_id=resp.unit_id,
                    function_code=resp.function_code,
                    protocol_control=resp.protocol_control,
                    index=resp.index,
                    subindex=resp.subindex,
                    byte_count=req_byte_count,
                    data=padded_data,
                    exception_code=resp.exception_code,
                )

    return resp
