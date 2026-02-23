"""Transport layer for dryve D1 Modbus TCP Gateway.

Responsibilities:
- TCP socket lifecycle (connect/reconnect/close) with sane timeouts
- Request/response framing for Modbus TCP (MBAP header length-based reads)
- Session orchestration: serialize requests (1 in-flight), keepalive, retry/backoff

The transport layer is intentionally protocol-agnostic: it sends/receives raw ADU bytes.
Protocol validation belongs to `dryve_d1.protocol`.
"""

from .clock import monotonic_ms, monotonic_s, sleep_s
from .modbus_tcp_client import ModbusTcpClient
from .retry import RetryBudget, RetryPolicy
from .session import ModbusSession, TransactionIdGenerator

__all__ = [
    "ModbusSession",
    "ModbusTcpClient",
    "RetryBudget",
    "RetryPolicy",
    "TransactionIdGenerator",
    "monotonic_ms",
    "monotonic_s",
    "sleep_s",
]
