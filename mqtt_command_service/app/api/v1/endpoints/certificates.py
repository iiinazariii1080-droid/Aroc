"""Certificate management endpoints."""
import asyncio
import logging
import os

from fastapi import APIRouter, File, Form, HTTPException, Security, UploadFile, status
from fastapi.responses import Response

from app.core.exceptions import CertificateError, ValidationError
from app.core.security import Role, require_auth
from app.models.schemas import CertificateBase64Upload, CertificateUploadResponse, MultipleCertificatesUploadResponse
from app.services.certificate_service import CertificateService
from app.services.config_service import get_config_service

logger = logging.getLogger(__name__)

router = APIRouter()
certificate_service = CertificateService()


@router.get(
    "/config/certificates/ca",
    summary="Get MQTT broker CA certificate",
    description="""
    Returns the CA certificate for the MQTT broker.

    This endpoint allows clients to download the CA certificate needed to connect
    to the MQTT broker via TLS. No authentication required.

    **Returns:**
    - CA certificate in PEM format
    - Content-Type: application/x-pem-file or text/plain

    **Example:**
    ```bash
    curl http://robot-ip:7900/api/v1/config/certificates/ca > mqtt-broker-ca.crt
    ```
    """,
    responses={
        200: {
            "description": "CA certificate in PEM format",
            "content": {
                "application/x-pem-file": {},
                "text/plain": {}
            }
        },
        404: {
            "description": "CA certificate not found"
        }
    }
)
async def get_ca_certificate() -> Response:
    """
    Get CA certificate for MQTT broker.

    Returns the CA certificate file that clients need to verify the broker's
    TLS certificate. This is a public endpoint (no authentication required).
    """
    try:
        # Certificate paths are fixed - always use certs/ directory
        from constants import CERT_CA_FILE
        ca_cert_path = str(CERT_CA_FILE)

        if not os.path.exists(ca_cert_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="CA certificate not found. Please upload it first via /config/certificates/upload"
            )

        # Read certificate file in binary mode (supports both PEM and DER)
        with open(ca_cert_path, 'rb') as f:
            cert_content = f.read()

        logger.info("CA certificate requested, returning from %s", ca_cert_path)

        # Auto-detect format: PEM starts with '-----'
        is_pem = cert_content.startswith(b'-----')
        media_type = "application/x-pem-file" if is_pem else "application/x-x509-ca-cert"

        return Response(
            content=cert_content,
            media_type=media_type,
            headers={
                "Content-Disposition": 'attachment; filename="mqtt-broker-ca.crt"'
            }
        )

    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CA certificate file not found"
        )
    except Exception as e:
        logger.error("Failed to get CA certificate: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve CA certificate: {e!s}"
        )



@router.post(
    "/config/certificates/upload",
    response_model=CertificateUploadResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload TLS certificate file",
    description="""
    Uploads a single certificate file to the robot via REST API.

    Supported certificate types:
    - `ca_cert`: CA certificate (for broker certificate verification)
    - `client_cert`: Client certificate (for mutual TLS)
    - `client_key`: Client private key (for mutual TLS)

    Files are saved to a secure directory and configuration is automatically updated.
    """,
    responses={
        200: {
            "description": "Certificate file uploaded successfully",
        },
        400: {
            "description": "Invalid file type or validation error",
        },
        401: {
            "description": "Unauthorized - missing or invalid API key",
        },
        403: {
            "description": "Forbidden - insufficient permissions (requires WRITE role)",
        },
        500: {
            "description": "File save error",
        }
    }
)
async def upload_certificate(
    cert_type: str = Form(..., description="Certificate type: ca_cert, client_cert, or client_key"),
    file: UploadFile = File(..., description="Certificate file"),
    auto_update_config: bool = Form(default=True, description="Auto-update configuration"),
    role: Role = Security(require_auth(Role.WRITE))
) -> CertificateUploadResponse:
    """
    Upload a single TLS certificate file.

    **Authentication:**
    - Requires API key with WRITE role or higher

    **Supported certificate types:**
    - `ca_cert`: CA certificate (for broker certificate verification)
    - `client_cert`: Client certificate (for mutual TLS)
    - `client_key`: Client private key (for mutual TLS)

    **File validation:**
    - File extension must be one of: `.crt`, `.pem`, `.key`, `.cer`
    - File size must not exceed 1MB
    - File content must be valid certificate format
    - Path traversal protection (no `../` in filename)

    **Example request:**
    ```bash
    curl -X POST "http://localhost:7900/api/v1/config/certificates/upload" \
      -H "X-API-Key: your-write-key" \
      -F "cert_type=ca_cert" \
      -F "file=@/path/to/ca.crt" \
      -F "auto_update_config=true"
    ```

    **Example response:**
    ```json
    {
      "success": true,
      "file_path": "/app/certs/ca.crt",
      "cert_type": "ca_cert",
      "message": "Certificate uploaded successfully"
    }
    ```
    """
    try:
        return await certificate_service.upload_certificate_file(
            cert_type=cert_type,
            file=file,
            auto_update_config=auto_update_config
        )
    except ValueError as e:
        # ValueError raised by get_cert_filename_and_config_key for invalid cert_type
        raise ValidationError(str(e))


@router.post(
    "/config/certificates/upload-multiple",
    response_model=MultipleCertificatesUploadResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload multiple certificate files at once",
    description="""
    Uploads multiple certificate files at once.

    Can upload:
    - CA certificate
    - Client certificate
    - Client key

    All files are uploaded atomically - either all succeed or rollback.
    """,
    responses={
        200: {
            "description": "Certificates uploaded successfully",
        },
        400: {
            "description": "Invalid file type or validation error",
        },
        401: {
            "description": "Unauthorized - missing or invalid API key",
        },
        403: {
            "description": "Forbidden - insufficient permissions (requires WRITE role)",
        },
        500: {
            "description": "File save error",
        }
    }
)
async def upload_multiple_certificates(
    ca_cert: UploadFile | None = File(None, description="CA certificate file"),
    client_cert: UploadFile | None = File(None, description="Client certificate file"),
    client_key: UploadFile | None = File(None, description="Client private key file"),
    auto_update_config: bool = Form(default=True, description="Auto-update configuration"),
    role: Role = Security(require_auth(Role.WRITE))
) -> MultipleCertificatesUploadResponse:
    """
    Upload multiple certificate files simultaneously.

    **Authentication:**
    - Requires API key with WRITE role or higher

    **Features:**
    - Can upload CA certificate, client certificate, and client key in one request
    - All files are uploaded atomically - either all succeed or all rollback
    - Configuration is updated only after all files are successfully uploaded

    **Example request:**
    ```bash
    curl -X POST "http://localhost:7900/api/v1/config/certificates/upload-multiple" \
      -H "X-API-Key: your-write-key" \
      -F "ca_cert=@/path/to/ca.crt" \
      -F "client_cert=@/path/to/client.crt" \
      -F "client_key=@/path/to/client.key" \
      -F "auto_update_config=true"
    ```

    **Example response:**
    ```json
    {
      "success": true,
      "uploaded_files": {
        "ca_cert": "/app/certs/ca.crt",
        "client_cert": "/app/certs/client.crt",
        "client_key": "/app/certs/client.key"
      },
      "message": "All certificates uploaded successfully"
    }
    ```
    """
    uploaded_files = {}
    errors = []

    # Validate that at least one file was provided
    if not any([ca_cert, client_cert, client_key]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one certificate file must be provided"
        )

    try:
        # Upload CA certificate
        if ca_cert:
            try:
                result = await certificate_service.upload_certificate_file(
                    cert_type="ca_cert",
                    file=ca_cert,
                    auto_update_config=False  # Will update config at the end
                )
                uploaded_files["ca_cert"] = result.file_path
            except Exception as e:
                errors.append(f"ca_cert: {e!s}")

        # Upload client certificate
        if client_cert:
            try:
                result = await certificate_service.upload_certificate_file(
                    cert_type="client_cert",
                    file=client_cert,
                    auto_update_config=False
                )
                uploaded_files["client_cert"] = result.file_path
            except Exception as e:
                errors.append(f"client_cert: {e!s}")

        # Upload client key
        if client_key:
            try:
                result = await certificate_service.upload_certificate_file(
                    cert_type="client_key",
                    file=client_key,
                    auto_update_config=False
                )
                uploaded_files["client_key"] = result.file_path
            except Exception as e:
                errors.append(f"client_key: {e!s}")

        # If there were errors, rollback changes
        if errors:
            # Delete uploaded files
            for file_path in uploaded_files.values():
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except Exception:
                    pass

            raise CertificateError(f"Failed to upload some certificates: {', '.join(errors)}")

        # Update configuration if requested (atomically)
        if auto_update_config and uploaded_files:
            updates = {}
            if "ca_cert" in uploaded_files:
                updates["MQTT_CA_CERTS"] = uploaded_files["ca_cert"]
            # Client cert/key are optional - update them if provided
            # They will only be used if both cert and key are present
            if "client_cert" in uploaded_files:
                updates["MQTT_CERTFILE"] = uploaded_files["client_cert"]
            if "client_key" in uploaded_files:
                updates["MQTT_KEYFILE"] = uploaded_files["client_key"]

            # Apply all updates atomically via ConfigService (triggers revision + notifications)
            config_service = get_config_service()
            if not config_service.update_config(
                updates,
                updated_by="api",
                reason="Certificates uploaded via API (multiple)"
            ):
                logger.warning(
                    "Failed to update config for multiple certificates, but files were saved"
                )

        message = f"Successfully uploaded {len(uploaded_files)} certificate file(s)"
        if auto_update_config:
            message += ". MQTT connection will be reloaded automatically within 30 seconds."

        return MultipleCertificatesUploadResponse(
            success=True,
            uploaded_files=uploaded_files,
            auto_updated_config=auto_update_config,
            message=message
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to upload multiple certificates: %s", e, exc_info=True)
        raise CertificateError(f"Failed to upload certificates: {e!s}")


@router.post(
    "/config/certificates/upload-base64",
    response_model=CertificateUploadResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload certificate via base64 (for MQTT)",
    description="""
    Uploads certificate via base64-encoded JSON.

    Useful for uploading via MQTT, where files are transmitted in base64.
    """,
    responses={
        200: {
            "description": "Certificate uploaded successfully",
        },
        400: {
            "description": "Invalid base64 encoding or validation error",
        },
        401: {
            "description": "Unauthorized - missing or invalid API key",
        },
        403: {
            "description": "Forbidden - insufficient permissions (requires WRITE role)",
        },
        500: {
            "description": "File save error",
        }
    }
)
async def upload_certificate_base64(
    request: CertificateBase64Upload,
    role: Role = Security(require_auth(Role.WRITE))
) -> CertificateUploadResponse:
    """
    Uploads certificate from base64-encoded string.
    """
    return await asyncio.to_thread(
        certificate_service.upload_certificate_base64,
        cert_type=request.cert_type,
        content_base64=request.content_base64,
        filename=request.filename,
        auto_update_config=request.auto_update_config,
    )

