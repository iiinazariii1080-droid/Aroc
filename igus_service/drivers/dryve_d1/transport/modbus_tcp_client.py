"""Low-level Modbus TCP client (socket wrapper).

This component is intentionally minimal:
- connect / close
- transceive raw ADU bytes
- receive framing based on MBAP length field

Higher-level concerns (serialization, keepalive, retries) live in `session.py`.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TcpConfig:
    host: str
    port: int = 502
    connect_timeout_s: float = 3.0
    io_timeout_s: float = 2.0
    tcp_nodelay: bool = True
    so_keepalive: bool = True


class ModbusTcpClient:
    """A small, robust Modbus TCP client for raw ADU exchange."""

    def __init__(self, config: TcpConfig, *, logger=None) -> None:
        self._cfg = config
        self._sock: socket.socket | None = None
        self._log = logger

    @property
    def is_connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        if self._sock is not None:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.settimeout(self._cfg.connect_timeout_s)
            s.connect((self._cfg.host, self._cfg.port))
            s.settimeout(self._cfg.io_timeout_s)

            if self._cfg.tcp_nodelay:
                try:
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    pass
            if self._cfg.so_keepalive:
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                except OSError:
                    pass

            self._sock = s
        except Exception:
            try:
                s.close()
            except Exception:
                pass
            raise

    def close(self) -> None:
        s = self._sock
        self._sock = None
        if s is None:
            return
        try:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()
        finally:
            self._sock = None

    def _recv_exactly(self, n: int) -> bytes:
        if self._sock is None:
            raise ConnectionError("Socket is not connected")
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Connection closed by peer")
            buf.extend(chunk)
        return bytes(buf)

    def transceive(self, adu: bytes) -> bytes:
        """Send request ADU and receive full response ADU.

        Response framing uses MBAP length field (bytes 4..5, big endian):
          length = number of bytes following the length field (UnitId + PDU)
        We read:
          - 7 bytes MBAP header (including UnitId)
          - (length - 1) bytes remaining PDU
        """
        if self._sock is None:
            raise ConnectionError("Socket is not connected")

        if not isinstance(adu, (bytes, bytearray)):
            raise TypeError("adu must be bytes-like")
        adu = bytes(adu)

        # Send request
        self._sock.sendall(adu)

        # Receive MBAP header
        hdr = self._recv_exactly(7)
        
        length = (hdr[4] << 8) | hdr[5]
        if length < 1:
            raise ConnectionError(f"Invalid MBAP length: {length}")
        # Remaining bytes after UnitId already read in header
        to_read = length - 1
        pdu = self._recv_exactly(to_read) if to_read else b""
        resp = hdr + pdu
        
        return resp
