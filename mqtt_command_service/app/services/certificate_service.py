"""Certificate management service."""
import base64
import contextlib
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile, status

from app.models.schemas import CertificateUploadResponse
from app.utils.certificate import (
    check_disk_space,
    get_cert_filename_and_config_key,
    set_certificate_permissions,
    validate_certificate_content,
)
from constants import (
    ALLOWED_CERT_EXTENSIONS,
    MAX_BASE64_SIZE,
    MAX_CERT_SIZE,
)

logger = logging.getLogger(__name__)


class CertificateService:
    """Service for managing certificate files."""

    def __init__(self, storage_dir: Path | None = None):
        # Always use fixed certs/ directory in application root
        from constants import DEFAULT_CERT_STORAGE_DIR
        self.storage_dir = storage_dir or DEFAULT_CERT_STORAGE_DIR
        # Ensure directory exists
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _upload_certificate_common(
        self,
        cert_type: str,
        content: bytes,
        filename: str | None = None,
        auto_update_config: bool = True,
        reason_prefix: str = "Certificate uploaded via API"
    ) -> dict[str, Any]:
        """
        Common logic for uploading certificate (from file or base64).

        Args:
            cert_type: Type of certificate
            content: Certificate content as bytes
            filename: Optional filename (auto-generated if not provided)
            auto_update_config: Whether to update config
            reason_prefix: Prefix for config update reason

        Returns:
            Dictionary with upload result
        """
        # Check file size FIRST (before validation to give better error message)
        if len(content) > MAX_CERT_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File too large: {len(content)} bytes (max {MAX_CERT_SIZE})"
            )

        # Check disk space
        if not check_disk_space(len(content)):
            raise HTTPException(
                status_code=507,  # Insufficient Storage
                detail="Insufficient storage space"
            )

        # Validate certificate content
        try:
            validate_certificate_content(content, cert_type)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid certificate: {e!s}"
            )

        # Determine filename and path
        default_filename, _config_key = get_cert_filename_and_config_key(cert_type)
        filename = filename or default_filename

        # Filename validation — protect against path traversal
        if not filename or filename != os.path.basename(filename):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid filename: path traversal not allowed"
            )

        # Extension check
        if not filename.endswith(('.crt', '.pem', '.key', '.cer')):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file extension. Allowed: .crt, .pem, .key, .cer"
            )

        file_path = self.storage_dir / filename

        # Extra check — path must be inside storage_dir
        try:
            file_path.resolve().relative_to(self.storage_dir.resolve())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file path: outside storage directory"
            )

        # Atomic write through temporary file
        temp_path = file_path.with_suffix(file_path.suffix + '.tmp')
        try:
            # Write to temporary file
            with open(temp_path, "wb") as f:
                f.write(content)

            # Validate written file
            with open(temp_path, "rb") as f:
                written_content = f.read()
                validate_certificate_content(written_content, cert_type)

            # Set permissions before moving
            set_certificate_permissions(temp_path, cert_type)

            # On Windows, replace() may fail if target file is open or locked
            # Create backup of existing file before overwrite
            backup_path = file_path.with_suffix(file_path.suffix + '.bak')
            if file_path.exists():
                try:
                    import shutil
                    shutil.copy2(file_path, backup_path)
                except (PermissionError, OSError) as e:
                    logger.warning("Could not create backup of %s: %s", file_path, e)

                try:
                    # Try to remove old file (may fail if file is open)
                    file_path.unlink()
                except (PermissionError, OSError) as e:
                    # If removal fails, try replace anyway (works on some systems)
                    logger.warning("Could not remove old certificate file %s: %s. Trying replace...", file_path, e)

            # Atomic move/replace
            try:
                temp_path.replace(file_path)
            except (PermissionError, OSError) as e:
                # If replace fails (e.g., file is locked), try copy + delete
                logger.warning("Replace failed for %s: %s. Trying copy + delete...", file_path, e)
                import shutil
                shutil.copy2(temp_path, file_path)
                temp_path.unlink()
                # Set permissions on final file
                set_certificate_permissions(file_path, cert_type)

            logger.info("Certificate uploaded: %s (type: %s)", file_path, cert_type)

            # Remove backup after successful write
            if backup_path.exists():
                with contextlib.suppress(OSError):
                    backup_path.unlink()

        except Exception as e:
            # Cleanup temp file on error
            temp_path.unlink(missing_ok=True)
            logger.error("Failed to save certificate file: %s", e, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to save certificate file: {e!s}"
            )

        # Certificate paths are fixed - no need to update config
        # Files are always stored in certs/ directory
        saved_path = str(file_path)
        # Note: Config update removed - paths are fixed in constants

        return {
            "success": True,
            "cert_type": cert_type,
            "file_path": saved_path,
            "filename": filename,
            "auto_updated_config": auto_update_config,
            "message": f"Certificate {cert_type} uploaded successfully"
        }

    async def upload_certificate_file(
        self,
        cert_type: str,
        file: UploadFile,
        auto_update_config: bool = True
    ) -> CertificateUploadResponse:
        """
        Upload certificate file.

        Args:
            cert_type: Certificate type (ca_cert, client_cert, client_key)
            file: File to upload
            auto_update_config: Automatically update configuration

        Returns:
            Upload result with file paths
        """
        try:
            # Validate file extension
            file_ext = Path(file.filename).suffix.lower() if file.filename else ''
            if file_ext not in ALLOWED_CERT_EXTENSIONS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid file extension. Allowed: {', '.join(ALLOWED_CERT_EXTENSIONS)}"
                )

            # Read file content in chunks (with size limit)
            chunk_size = 64 * 1024  # 64KB chunks
            total_read = 0
            content = b""

            while True:
                # Check size BEFORE reading next chunk
                if total_read >= MAX_CERT_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"File too large: exceeds {MAX_CERT_SIZE} bytes limit"
                    )

                remaining = MAX_CERT_SIZE - total_read
                chunk = await file.read(min(chunk_size, remaining))
                if not chunk:
                    break

                content += chunk
                total_read += len(chunk)

                # Check size AFTER reading chunk
                if total_read > MAX_CERT_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"File too large: {total_read} bytes (max {MAX_CERT_SIZE})"
                    )

            # Use common upload logic
            result_dict = self._upload_certificate_common(
                cert_type=cert_type,
                content=content,
                filename=None,  # Use default filename from cert_type
                auto_update_config=auto_update_config,
                reason_prefix="Certificate uploaded via API"
            )

            # Convert dict to Pydantic model
            return CertificateUploadResponse(**result_dict)

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to upload certificate: %s", e, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to upload certificate: {e!s}"
            )

    def upload_certificate_base64(
        self,
        cert_type: str,
        content_base64: str,
        filename: str | None = None,
        auto_update_config: bool = True
    ) -> CertificateUploadResponse:
        """
        Upload certificate from base64-encoded string.

        Args:
            cert_type: Certificate type (ca_cert, client_cert, client_key)
            content_base64: Base64-encoded certificate content
            filename: Optional filename (auto-generated if not provided)
            auto_update_config: Automatically update configuration

        Returns:
            Upload result
        """
        try:
            # Validate base64 size
            if len(content_base64) > MAX_BASE64_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Base64 content too large: {len(content_base64)} (max {MAX_BASE64_SIZE})"
                )

            # Decode base64
            try:
                content = base64.b64decode(content_base64, validate=True)
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid base64 encoding: {e!s}"
                )

            # Use common upload logic
            result_dict = self._upload_certificate_common(
                cert_type=cert_type,
                content=content,
                filename=filename,
                auto_update_config=auto_update_config,
                reason_prefix="Certificate uploaded via base64 API"
            )
            # Update message for base64 upload
            result_dict["message"] = f"Certificate {cert_type} uploaded successfully via base64"

            # Convert dict to Pydantic model
            return CertificateUploadResponse(**result_dict)

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to upload certificate via base64: %s", e, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to upload certificate: {e!s}"
            )


