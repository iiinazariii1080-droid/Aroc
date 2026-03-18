"""Extract robot identity from X.509 client certificate.

Used in mTLS auth mode to derive robot_id from the certificate CN
instead of relying on environment variables.
"""

import logging
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID

from shared.env import validate_robot_id

logger = logging.getLogger(__name__)


def extract_cn_from_cert(cert_path: str) -> str:
    """Read PEM certificate at *cert_path* and return the Common Name (CN).

    The CN is validated against the robot_id format rules.

    Raises:
        FileNotFoundError: if cert_path does not exist.
        ValueError: if the cert has no CN or the CN is not a valid robot_id.
    """
    path = Path(cert_path)
    if not path.exists():
        raise FileNotFoundError(f"Certificate not found: {cert_path}")

    cert_data = path.read_bytes()
    cert = x509.load_pem_x509_certificate(cert_data)

    cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if not cn_attrs:
        raise ValueError(f"No CN (Common Name) in certificate subject: {cert_path}")

    cn = cn_attrs[0].value
    if not isinstance(cn, str) or not cn.strip():
        raise ValueError(f"Empty CN in certificate: {cert_path}")

    # Validate that CN is a legal robot_id
    validate_robot_id(cn)
    return cn
