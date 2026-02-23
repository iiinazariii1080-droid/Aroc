"""Certificate validation and utility functions."""
import os
import shutil
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from constants import (
    CERT_FILE_PERMISSIONS,
    DEFAULT_CERT_STORAGE_DIR,
    MAX_CERT_SIZE,
    PRIVATE_KEY_PERMISSIONS,
)

# Mapping cert_type -> (filename, config_key)
# Certificate paths are fixed - config_key is kept for backward compatibility but not used
CERT_TYPE_MAPPING = {
    "ca_cert": ("ca.crt", "MQTT_CA_CERTS"),  # config_key not used anymore
    "client_cert": ("client.crt", "MQTT_CERTFILE"),  # config_key not used anymore
    "client_key": ("client.key", "MQTT_KEYFILE"),  # config_key not used anymore
}


def get_cert_filename_and_config_key(cert_type: str) -> tuple[str, str]:
    """
    Get filename and config key for certificate type.

    Args:
        cert_type: Type of certificate (ca_cert, client_cert, client_key)

    Returns:
        (filename, config_key) tuple

    Raises:
        ValueError: If cert_type is invalid
    """
    if cert_type not in CERT_TYPE_MAPPING:
        raise ValueError(
            f"Invalid cert_type: {cert_type}. "
            f"Must be one of: {', '.join(CERT_TYPE_MAPPING.keys())}"
        )

    return CERT_TYPE_MAPPING[cert_type]


def validate_certificate_content(content: bytes, cert_type: str) -> bool:
    """
    Validate certificate content.

    Args:
        content: Certificate file content
        cert_type: Type of certificate (ca_cert, client_cert, client_key)

    Returns:
        True if valid

    Raises:
        ValueError: If certificate is invalid
    """
    if len(content) > MAX_CERT_SIZE:
        raise ValueError(f"Certificate file too large: {len(content)} bytes (max {MAX_CERT_SIZE})")

    try:
        if cert_type in ("ca_cert", "client_cert"):
            # Try to load as PEM certificate
            try:
                x509.load_pem_x509_certificate(content)
                return True
            except ValueError:
                # Try DER format
                try:
                    x509.load_der_x509_certificate(content)
                    return True
                except ValueError:
                    raise ValueError("Invalid certificate format. Expected PEM or DER.")

        elif cert_type == "client_key":
            # Try to load as PEM private key
            try:
                serialization.load_pem_private_key(content, password=None)
                return True
            except ValueError:
                # Try DER format
                try:
                    serialization.load_der_private_key(content, password=None)
                    return True
                except ValueError:
                    raise ValueError("Invalid private key format. Expected PEM or DER.")

        return False

    except Exception as e:
        if isinstance(e, ValueError):
            raise
        raise ValueError(f"Failed to validate certificate: {e!s}")


def check_disk_space(required_bytes: int) -> bool:
    """Check if enough disk space is available."""
    stat = shutil.disk_usage(DEFAULT_CERT_STORAGE_DIR)
    # Require at least 3x the file size as free space (safety margin)
    return stat.free >= required_bytes * 3


def set_certificate_permissions(file_path: Path, cert_type: str) -> None:
    """Set appropriate file permissions for certificate files."""
    if cert_type == "client_key":
        os.chmod(file_path, PRIVATE_KEY_PERMISSIONS)
    else:
        os.chmod(file_path, CERT_FILE_PERMISSIONS)

