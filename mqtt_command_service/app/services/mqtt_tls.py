"""TLS certificate validation and configuration for MQTT clients."""
import logging
from pathlib import Path

from paho.mqtt import client as mqtt_client

logger = logging.getLogger(__name__)


def normalize_cert_paths(
    ca_certs: str | None,
    certfile: str | None,
    keyfile: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Normalize certificate file paths (strip whitespace, convert empty to None).

    Args:
        ca_certs: Path to CA certificate file.
        certfile: Path to client certificate file.
        keyfile: Path to client key file.

    Returns:
        Tuple of (normalized_ca_certs, normalized_certfile, normalized_keyfile).
        Empty strings are converted to ``None``.
    """
    ca_normalized = ca_certs.strip() if (ca_certs and ca_certs.strip()) else None
    cert_normalized = certfile.strip() if (certfile and certfile.strip()) else None
    key_normalized = keyfile.strip() if (keyfile and keyfile.strip()) else None
    return ca_normalized, cert_normalized, key_normalized


def validate_tls_certificates(
    ca_certs: str | None,
    certfile: str | None,
    keyfile: str | None,
) -> list[str]:
    """Validate TLS certificate files exist and are properly configured.

    Args:
        ca_certs: Path to CA certificate (already normalized).
        certfile: Path to client certificate (already normalized).
        keyfile: Path to client key (already normalized).

    Returns:
        List of error messages (empty if all valid).
    """
    missing_files: list[str] = []

    if ca_certs and not Path(ca_certs).exists():
        missing_files.append(f"CA cert: {ca_certs}")
    if certfile and not Path(certfile).exists():
        missing_files.append(f"Client cert: {certfile}")
    if keyfile and not Path(keyfile).exists():
        missing_files.append(f"Client key: {keyfile}")

    # Client cert and key must both be specified or both omitted
    if (certfile and not keyfile) or (keyfile and not certfile):
        missing_files.append("Client cert and key must both be specified or both omitted")

    return missing_files


def configure_tls_on_client(
    client: mqtt_client.Client,
    ca_certs: str | None,
    certfile: str | None,
    keyfile: str | None,
    tls_insecure: bool,
) -> None:
    """Configure TLS on an MQTT client instance.

    Args:
        client: A paho MQTT ``Client`` instance.
        ca_certs: Path to CA certificate file (or ``None`` for system defaults).
        certfile: Path to client certificate file.
        keyfile: Path to client key file.
        tls_insecure: Whether to skip server certificate verification.

    Raises:
        FileNotFoundError: If a certificate file does not exist.
        Exception: For other TLS configuration errors.
    """
    if tls_insecure:
        client.tls_set(
            ca_certs=ca_certs,
            certfile=certfile,
            keyfile=keyfile,
            tls_version=mqtt_client.ssl.PROTOCOL_TLS,
        )
        client.tls_insecure_set(True)
    else:
        client.tls_set(
            ca_certs=ca_certs,
            certfile=certfile,
            keyfile=keyfile,
        )
