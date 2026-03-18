"""Certificate management endpoints."""

import base64
import contextlib
import logging
import os
import tempfile

from fastapi import APIRouter, File, Form, HTTPException, Security, UploadFile
from fastapi.responses import Response

from schemas import CertificateBase64Upload, CertificateUploadResponse, MultipleCertificatesUploadResponse
from security import Role, optional_auth, require_auth
from shared.constants import ALLOWED_CERT_EXTENSIONS, CERT_FILE_PERMISSIONS, MAX_CERT_SIZE, PRIVATE_KEY_PERMISSIONS
from shared.constants import cert_dir as _cert_dir

logger = logging.getLogger(__name__)

router = APIRouter()

CERT_TYPE_MAP = {
    "ca_cert": ("ca.crt", CERT_FILE_PERMISSIONS),
    "client_cert": ("client.crt", CERT_FILE_PERMISSIONS),
    "client_key": ("client.key", PRIVATE_KEY_PERMISSIONS),
}


_PEM_MARKERS = {
    "ca_cert": (b"-----BEGIN CERTIFICATE-----", b"-----END CERTIFICATE-----"),
    "client_cert": (b"-----BEGIN CERTIFICATE-----", b"-----END CERTIFICATE-----"),
    "client_key": (b"-----BEGIN", b"-----END"),  # Allows RSA/EC/PKCS8 private keys
}


def _validate_cert_content(cert_type: str, content: bytes) -> None:
    """Validate that the uploaded content looks like a valid PEM file for its type."""
    markers = _PEM_MARKERS.get(cert_type)
    if not markers:
        return
    begin_marker, end_marker = markers
    if not content.strip().startswith(begin_marker):
        raise ValueError(f"Invalid {cert_type}: expected PEM format starting with {begin_marker.decode()}")
    if end_marker not in content:
        raise ValueError(f"Invalid {cert_type}: missing PEM end marker {end_marker.decode()}")
    # Prevent uploading a private key as a certificate
    if cert_type in ("ca_cert", "client_cert") and b"PRIVATE KEY" in content:
        raise ValueError(f"Invalid {cert_type}: file contains a private key, expected a certificate")


def _save_cert(cert_type: str, content: bytes) -> str:
    """Save certificate to the cert directory. Returns the saved path."""
    if cert_type not in CERT_TYPE_MAP:
        raise ValueError(f"Invalid cert_type: {cert_type}. Must be one of: {', '.join(CERT_TYPE_MAP)}")

    _validate_cert_content(cert_type, content)

    filename, permissions = CERT_TYPE_MAP[cert_type]
    cert_path = _cert_dir() / filename
    cert_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write
    fd, tmp_path = tempfile.mkstemp(dir=str(cert_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp_path, str(cert_path))
        with contextlib.suppress(OSError):  # Windows doesn't support chmod the same way
            os.chmod(str(cert_path), permissions)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise

    return str(cert_path)


@router.get("/config/certificates/ca")
async def get_ca_certificate(
    role: Role = Security(optional_auth()),
) -> Response:
    ca_path = _cert_dir() / "ca.crt"
    if not ca_path.exists():
        raise HTTPException(status_code=404, detail="CA certificate not found")

    content = ca_path.read_bytes()
    is_pem = content.startswith(b"-----")
    media_type = "application/x-pem-file" if is_pem else "application/x-x509-ca-cert"

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": 'attachment; filename="ca.crt"'},
    )


@router.post("/config/certificates/upload", response_model=CertificateUploadResponse)
async def upload_certificate(
    cert_type: str = Form(...),
    file: UploadFile = File(...),
    role: Role = Security(require_auth(Role.WRITE)),
) -> CertificateUploadResponse:
    if cert_type not in CERT_TYPE_MAP:
        raise HTTPException(status_code=400, detail=f"Invalid cert_type: {cert_type}")

    if file.filename:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext and ext not in ALLOWED_CERT_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Invalid file extension: {ext}")

    content = await file.read()
    if len(content) > MAX_CERT_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large (max {MAX_CERT_SIZE} bytes)")

    try:
        _validate_cert_content(cert_type, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    saved_path = _save_cert(cert_type, content)
    filename = CERT_TYPE_MAP[cert_type][0]

    return CertificateUploadResponse(
        success=True,
        cert_type=cert_type,
        file_path=saved_path,
        filename=filename,
        message=f"Certificate '{cert_type}' uploaded successfully",
    )


@router.post("/config/certificates/upload-multiple", response_model=MultipleCertificatesUploadResponse)
async def upload_multiple_certificates(
    ca_cert: UploadFile | None = File(None),
    client_cert: UploadFile | None = File(None),
    client_key: UploadFile | None = File(None),
    role: Role = Security(require_auth(Role.WRITE)),
) -> MultipleCertificatesUploadResponse:
    if not any([ca_cert, client_cert, client_key]):
        raise HTTPException(status_code=400, detail="At least one certificate file must be provided")

    uploaded: dict[str, str] = {}
    backups: dict[str, str] = {}
    errors: list[str] = []

    for cert_type, upload_file in [("ca_cert", ca_cert), ("client_cert", client_cert), ("client_key", client_key)]:
        if upload_file is None:
            continue
        try:
            content = await upload_file.read()
            if len(content) > MAX_CERT_SIZE:
                errors.append(f"{cert_type}: file too large")
                continue

            # Backup existing file before overwriting
            filename = CERT_TYPE_MAP[cert_type][0]
            original_path = _cert_dir() / filename
            if original_path.exists():
                backup_path = str(original_path) + ".bak"
                try:
                    import shutil

                    shutil.copy2(str(original_path), backup_path)
                    backups[cert_type] = backup_path
                except OSError as backup_err:
                    errors.append(f"{cert_type}: failed to backup original: {backup_err}")
                    continue

            path = _save_cert(cert_type, content)
            uploaded[cert_type] = path
        except Exception as e:
            errors.append(f"{cert_type}: {e}")

    if errors:
        # Rollback: restore backups atomically FIRST (os.replace overwrites
        # the destination in one step — no window where cert file is absent),
        # then remove newly-created files that had no backup.
        rollback_failures: list[str] = []
        for cert_type, backup_path in backups.items():
            filename = CERT_TYPE_MAP[cert_type][0]
            original_path = _cert_dir() / filename
            try:
                os.replace(backup_path, str(original_path))
            except OSError as e:
                rollback_failures.append(f"failed to restore {cert_type}: {e}")
        for cert_type, path in uploaded.items():
            if cert_type not in backups:
                # Newly created file (no original existed) — remove it
                with contextlib.suppress(OSError):
                    os.unlink(path)
        if rollback_failures:
            logger.critical(
                "Certificate rollback partially failed — certificates may be inconsistent: %s",
                "; ".join(rollback_failures),
            )
        detail = f"Upload errors: {', '.join(errors)}"
        if rollback_failures:
            detail += f" (rollback also failed: {'; '.join(rollback_failures)})"
        raise HTTPException(status_code=400 if not rollback_failures else 500, detail=detail)

    # Clean up backup files on success
    for backup_path in backups.values():
        with contextlib.suppress(OSError):
            os.unlink(backup_path)

    return MultipleCertificatesUploadResponse(
        success=True,
        uploaded_files=uploaded,
        message=f"Successfully uploaded {len(uploaded)} certificate file(s)",
    )


@router.post("/config/certificates/upload-base64", response_model=CertificateUploadResponse)
async def upload_certificate_base64(
    request: CertificateBase64Upload,
    role: Role = Security(require_auth(Role.WRITE)),
) -> CertificateUploadResponse:
    try:
        content = base64.b64decode(request.content_base64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid base64 encoding") from exc

    if len(content) > MAX_CERT_SIZE:
        raise HTTPException(status_code=400, detail="Decoded content too large")

    try:
        _validate_cert_content(request.cert_type, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    saved_path = _save_cert(request.cert_type, content)
    filename = CERT_TYPE_MAP[request.cert_type][0]

    return CertificateUploadResponse(
        success=True,
        cert_type=request.cert_type,
        file_path=saved_path,
        filename=filename,
        message=f"Certificate '{request.cert_type}' uploaded from base64",
    )
