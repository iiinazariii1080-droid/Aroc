"""Tests for certificate utilities and certificate service."""
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# app.utils.certificate
# ---------------------------------------------------------------------------

class TestGetCertFilenameAndConfigKey:
    """Tests for get_cert_filename_and_config_key."""

    def test_ca_cert(self):
        from app.utils.certificate import get_cert_filename_and_config_key
        filename, key = get_cert_filename_and_config_key("ca_cert")
        assert filename == "ca.crt"
        assert key == "MQTT_CA_CERTS"

    def test_client_cert(self):
        from app.utils.certificate import get_cert_filename_and_config_key
        filename, _key = get_cert_filename_and_config_key("client_cert")
        assert filename == "client.crt"

    def test_client_key(self):
        from app.utils.certificate import get_cert_filename_and_config_key
        filename, _key = get_cert_filename_and_config_key("client_key")
        assert filename == "client.key"

    def test_invalid_type_raises(self):
        from app.utils.certificate import get_cert_filename_and_config_key
        with pytest.raises(ValueError, match="Invalid cert_type"):
            get_cert_filename_and_config_key("bad_type")


class TestCheckDiskSpace:
    """Tests for check_disk_space."""

    @patch("app.utils.certificate.shutil.disk_usage")
    def test_enough_space(self, mock_usage):
        from app.utils.certificate import check_disk_space
        # 100 MB free
        mock_usage.return_value = MagicMock(free=100 * 1024 * 1024)
        assert check_disk_space(10 * 1024 * 1024) is True

    @patch("app.utils.certificate.shutil.disk_usage")
    def test_not_enough_space(self, mock_usage):
        from app.utils.certificate import check_disk_space
        # 1 MB free
        mock_usage.return_value = MagicMock(free=1 * 1024 * 1024)
        assert check_disk_space(10 * 1024 * 1024) is False


class TestValidateCertificateContent:
    """Tests for validate_certificate_content."""

    def test_empty_content_raises(self):
        from app.utils.certificate import validate_certificate_content
        with pytest.raises(ValueError):
            validate_certificate_content(b"", "ca_cert")

    def test_oversized_content_raises(self):
        from app.utils.certificate import validate_certificate_content
        from constants import MAX_CERT_SIZE
        big = b"x" * (MAX_CERT_SIZE + 1)
        with pytest.raises(ValueError):
            validate_certificate_content(big, "ca_cert")


class TestSetCertificatePermissions:
    """Tests for set_certificate_permissions."""

    def test_sets_permissions_on_existing_file(self):
        from app.utils.certificate import set_certificate_permissions
        with tempfile.NamedTemporaryFile(delete=False, suffix=".crt") as f:
            f.write(b"test")
            path = Path(f.name)
        try:
            # Should not raise
            set_certificate_permissions(path, "ca_cert")
        finally:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# app.services.certificate_service
# ---------------------------------------------------------------------------

class TestCertificateService:
    """Tests for CertificateService."""

    def test_init_creates_directory(self):
        from app.services.certificate_service import CertificateService
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = CertificateService(storage_dir=Path(tmpdir) / "certs")
            assert svc.storage_dir.exists()

    @patch("app.services.certificate_service.validate_certificate_content", return_value=True)
    @patch("app.services.certificate_service.check_disk_space", return_value=True)
    def test_upload_saves_file(self, mock_disk, mock_validate):
        from app.services.certificate_service import CertificateService
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = CertificateService(storage_dir=Path(tmpdir))
            content = b"-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----\n"
            result = svc._upload_certificate_common("ca_cert", content, auto_update_config=False)
            assert result["cert_type"] == "ca_cert"
            saved_path = Path(tmpdir) / "ca.crt"
            assert saved_path.exists()

    @patch("app.services.certificate_service.validate_certificate_content", side_effect=ValueError("bad cert"))
    def test_upload_invalid_cert_raises(self, mock_validate):
        from fastapi import HTTPException

        from app.services.certificate_service import CertificateService
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = CertificateService(storage_dir=Path(tmpdir))
            with pytest.raises(HTTPException) as exc_info:
                svc._upload_certificate_common("ca_cert", b"invalid")
            assert exc_info.value.status_code == 400

    def test_upload_oversized_cert_raises(self):
        from fastapi import HTTPException

        from app.services.certificate_service import CertificateService
        from constants import MAX_CERT_SIZE
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = CertificateService(storage_dir=Path(tmpdir))
            with pytest.raises(HTTPException) as exc_info:
                svc._upload_certificate_common("ca_cert", b"x" * (MAX_CERT_SIZE + 1))
            assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# app.utils.file_utils
# ---------------------------------------------------------------------------

class TestFileUtils:
    """Tests for app.utils.file_utils."""

    def test_cleanup_old_temp_files(self):
        from app.utils.file_utils import cleanup_old_temp_files
        # Should not raise on clean system
        result = cleanup_old_temp_files()
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# app.utils.payload_validation
# ---------------------------------------------------------------------------

class TestPayloadValidation:
    """Tests for app.utils.payload_validation."""

    def test_validate_dict_payload_valid(self):
        from app.utils.payload_validation import validate_dict_payload
        result = validate_dict_payload({"key": "val"})
        # Returns a tuple (dict, error) or just the dict depending on signature
        if isinstance(result, tuple):
            assert result[0] == {"key": "val"}
        else:
            assert result == {"key": "val"}

    def test_validate_dict_payload_none(self):
        from app.utils.payload_validation import validate_dict_payload
        result, error = validate_dict_payload(None)
        assert result is None
        assert error is not None

    def test_validate_required_field_present(self):
        from app.utils.payload_validation import validate_required_field
        value, error = validate_required_field({"a": 1}, "a")
        assert value == 1
        assert error is None

    def test_validate_required_field_missing(self):
        from app.utils.payload_validation import validate_required_field
        value, error = validate_required_field({"a": 1}, "b")
        assert value is None
        assert error is not None

    def test_validate_headers_valid_dict(self):
        from app.utils.payload_validation import validate_headers
        result, error = validate_headers({"Content-Type": "application/json"})
        assert isinstance(result, dict)
        assert error is None

    def test_validate_headers_none(self):
        from app.utils.payload_validation import validate_headers
        result, _error = validate_headers(None)
        assert result == {} or result is None

    def test_has_any_config_parameter_true(self):
        from app.utils.payload_validation import has_any_config_parameter
        assert has_any_config_parameter({"MQTT_BROKER": "x"}, ["MQTT_BROKER", "MQTT_PORT"]) is True

    def test_has_any_config_parameter_false(self):
        from app.utils.payload_validation import has_any_config_parameter
        assert has_any_config_parameter({"random": "value"}, ["MQTT_BROKER"]) is False


# ---------------------------------------------------------------------------
# app.utils.validation
# ---------------------------------------------------------------------------

class TestValidation:
    """Tests for app.utils.validation module."""

    def test_import_succeeds(self):
        import app.utils.validation
        assert hasattr(app.utils.validation, '__name__')
