import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.certificate_service import CertificateService


@pytest.fixture
def cert_service(tmp_path: Path) -> CertificateService:
    # Use an isolated directory for each test run
    return CertificateService(storage_dir=tmp_path)


def _fake_validate_certificate_content(content: bytes, cert_type: str) -> bool:
    # We don't want to require real x509 / private key material in unit tests
    return True


def _fake_check_disk_space(required_bytes: int) -> bool:
    return True


def _fake_set_certificate_permissions(file_path: Path, cert_type: str) -> None:
    # No-op to avoid permission differences across platforms/CI
    return None


def test_upload_certificate_writes_file(cert_service: CertificateService, tmp_path: Path) -> None:
    with patch("app.services.certificate_service.validate_certificate_content", _fake_validate_certificate_content), \
         patch("app.services.certificate_service.check_disk_space", _fake_check_disk_space), \
         patch("app.services.certificate_service.set_certificate_permissions", _fake_set_certificate_permissions):
        result = cert_service._upload_certificate_common(
            cert_type="ca_cert",
            content=b"dummy-ca",
            filename=None,
            auto_update_config=True,
        )

    assert result["success"] is True
    assert Path(result["file_path"]).exists()
    assert Path(result["file_path"]).read_bytes() == b"dummy-ca"
    # filename is fixed by cert_type
    assert Path(result["file_path"]).name == "ca.crt"


def test_upload_certificate_replace_fallback_copy_when_replace_fails(cert_service: CertificateService, tmp_path: Path) -> None:
    """
    Emulate Windows-like replacement failure by forcing Path.replace to raise.
    Our service should fall back to copy2 + delete of temp file.
    """
    target = tmp_path / "ca.crt"
    target.write_bytes(b"old")

    real_path_replace = Path.replace
    real_copy2 = shutil.copy2

    def replace_side_effect(self: Path, target_path: Path):
        # Force failure only for the temp file -> target replacement
        if self.name.endswith(".tmp") and Path(target_path).name == "ca.crt":
            raise PermissionError("simulated replace failure")
        return real_path_replace(self, target_path)

    def copy2_side_effect(src: str | Path, dst: str | Path, *args, **kwargs):
        return real_copy2(src, dst, *args, **kwargs)

    with patch("app.services.certificate_service.validate_certificate_content", _fake_validate_certificate_content), \
         patch("app.services.certificate_service.check_disk_space", _fake_check_disk_space), \
         patch("app.services.certificate_service.set_certificate_permissions", _fake_set_certificate_permissions), \
         patch("pathlib.Path.replace", replace_side_effect), \
         patch("shutil.copy2", copy2_side_effect):
        result = cert_service._upload_certificate_common(
            cert_type="ca_cert",
            content=b"new",
            filename=None,
            auto_update_config=True,
        )

    assert result["success"] is True
    assert target.read_bytes() == b"new"
    assert (tmp_path / "ca.crt.tmp").exists() is False


def test_upload_certificate_attempts_delete_old_file_if_present(cert_service: CertificateService, tmp_path: Path) -> None:
    """
    Ensure we try to unlink old file before replace.
    We don't require actual OS-level locking for the unit test; we just assert unlink is called.
    """
    target = tmp_path / "ca.crt"
    target.write_bytes(b"old")

    called = {"unlink": 0}
    real_unlink = Path.unlink

    def unlink_side_effect(self: Path, *args, **kwargs):
        if self.name == "ca.crt":
            called["unlink"] += 1
        return real_unlink(self, *args, **kwargs)

    with patch("app.services.certificate_service.validate_certificate_content", _fake_validate_certificate_content), \
         patch("app.services.certificate_service.check_disk_space", _fake_check_disk_space), \
         patch("app.services.certificate_service.set_certificate_permissions", _fake_set_certificate_permissions), \
         patch("pathlib.Path.unlink", unlink_side_effect):
        cert_service._upload_certificate_common(
            cert_type="ca_cert",
            content=b"new",
            filename=None,
            auto_update_config=True,
        )

    assert called["unlink"] >= 1

