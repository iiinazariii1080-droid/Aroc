"""Tests for app/api/v1/endpoints/certificates.py — cert management."""
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def _bypass(bypass_auth):
    """All tests in this module run with auth bypassed."""


@pytest.fixture()
def api():
    with TestClient(app) as c:
        yield c


PEM_CONTENT = b"-----BEGIN CERTIFICATE-----\nMIIBkTCB+wIJ...\n-----END CERTIFICATE-----\n"
DER_CONTENT = b"\x30\x82\x01\x22"  # binary (non-PEM)


# ── GET /config/certificates/ca ────────────────────────────────────────


class TestGetCaCertificate:
    def test_pem_file(self, api, tmp_path):
        ca_file = tmp_path / "ca.crt"
        ca_file.write_bytes(PEM_CONTENT)

        with patch("constants.CERT_CA_FILE", ca_file), \
             patch("app.api.v1.endpoints.certificates.os.path.exists", return_value=True):
            resp = api.get("/api/v1/config/certificates/ca")
        assert resp.status_code == 200
        assert b"BEGIN CERTIFICATE" in resp.content

    def test_not_found(self, api, tmp_path):
        fake = tmp_path / "missing.crt"
        with patch("constants.CERT_CA_FILE", fake), \
             patch("app.api.v1.endpoints.certificates.os.path.exists", return_value=False):
            resp = api.get("/api/v1/config/certificates/ca")
        assert resp.status_code == 404


# ── POST /config/certificates/upload ──────────────────────────────────


class TestUploadCertificate:
    @patch("app.api.v1.endpoints.certificates.certificate_service")
    def test_upload_success(self, mock_svc, api):
        from app.models.schemas import CertificateUploadResponse

        mock_svc.upload_certificate_file = AsyncMock(
            return_value=CertificateUploadResponse(
                success=True,
                file_path="/app/certs/ca.crt",
                cert_type="ca_cert",
                filename="ca.crt",
                auto_updated_config=True,
                message="Certificate uploaded successfully",
            )
        )
        resp = api.post(
            "/api/v1/config/certificates/upload",
            data={"cert_type": "ca_cert", "auto_update_config": "true"},
            files={"file": ("ca.crt", PEM_CONTENT, "application/x-pem-file")},
        )
        assert resp.status_code == 200
        mock_svc.upload_certificate_file.assert_called_once()

    @patch("app.api.v1.endpoints.certificates.certificate_service")
    def test_upload_invalid_cert_type(self, mock_svc, api):
        mock_svc.upload_certificate_file = AsyncMock(
            side_effect=ValueError("Invalid certificate type")
        )
        resp = api.post(
            "/api/v1/config/certificates/upload",
            data={"cert_type": "bogus", "auto_update_config": "true"},
            files={"file": ("cert.crt", PEM_CONTENT, "application/x-pem-file")},
        )
        assert resp.status_code >= 400

    def test_upload_missing_file(self, api):
        resp = api.post(
            "/api/v1/config/certificates/upload",
            data={"cert_type": "ca_cert"},
        )
        assert resp.status_code == 422


# ── POST /config/certificates/upload-multiple ─────────────────────────


class TestUploadMultipleCertificates:
    @patch("app.api.v1.endpoints.certificates.certificate_service")
    @patch("app.api.v1.endpoints.certificates.get_config_service")
    def test_all_three(self, mock_get_svc, mock_cert_svc, api):
        config_svc = MagicMock()
        config_svc.update_config.return_value = True
        mock_get_svc.return_value = config_svc

        call_count = 0

        async def fake_upload(**kwargs):
            nonlocal call_count
            call_count += 1
            return MagicMock(
                success=True,
                file_path=f"/app/certs/{kwargs['cert_type']}.crt",
                cert_type=kwargs["cert_type"],
                message="OK",
            )

        mock_cert_svc.upload_certificate_file = fake_upload

        resp = api.post(
            "/api/v1/config/certificates/upload-multiple",
            data={"auto_update_config": "true"},
            files=[
                ("ca_cert", ("ca.crt", PEM_CONTENT, "application/x-pem-file")),
                ("client_cert", ("client.crt", PEM_CONTENT, "application/x-pem-file")),
                ("client_key", ("client.key", PEM_CONTENT, "application/x-pem-file")),
            ],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["uploaded_files"]) == 3

    def test_no_files_provided(self, api):
        resp = api.post(
            "/api/v1/config/certificates/upload-multiple",
            data={"auto_update_config": "true"},
        )
        assert resp.status_code == 400

    @patch("app.api.v1.endpoints.certificates.certificate_service")
    def test_partial_failure_rollback(self, mock_cert_svc, api):
        """If one cert fails, uploaded files should be rolled back."""
        call_idx = 0

        async def failing_upload(**kwargs):
            nonlocal call_idx
            call_idx += 1
            if kwargs["cert_type"] == "client_key":
                raise RuntimeError("disk full")
            return MagicMock(
                success=True,
                file_path=f"/tmp/{kwargs['cert_type']}.crt",
                cert_type=kwargs["cert_type"],
                message="OK",
            )

        mock_cert_svc.upload_certificate_file = failing_upload

        with patch("app.api.v1.endpoints.certificates.os.path.exists", return_value=True), \
             patch("app.api.v1.endpoints.certificates.os.remove") as mock_rm:
            resp = api.post(
                "/api/v1/config/certificates/upload-multiple",
                data={"auto_update_config": "true"},
                files=[
                    ("ca_cert", ("ca.crt", PEM_CONTENT, "application/x-pem-file")),
                    ("client_cert", ("client.crt", PEM_CONTENT, "application/x-pem-file")),
                    ("client_key", ("client.key", PEM_CONTENT, "application/x-pem-file")),
                ],
            )
            assert resp.status_code >= 400
            # Should have attempted to remove the 2 successfully uploaded files
            assert mock_rm.call_count >= 1


# ── POST /config/certificates/upload-base64 ───────────────────────────


class TestUploadCertificateBase64:
    @patch("app.api.v1.endpoints.certificates.certificate_service")
    def test_success(self, mock_svc, api):
        from app.models.schemas import CertificateUploadResponse

        mock_svc.upload_certificate_base64.return_value = CertificateUploadResponse(
            success=True,
            file_path="/app/certs/ca.crt",
            cert_type="ca_cert",
            filename="ca.crt",
            auto_updated_config=True,
            message="Certificate uploaded successfully",
        )
        resp = api.post(
            "/api/v1/config/certificates/upload-base64",
            json={
                "cert_type": "ca_cert",
                "content_base64": "LS0tLS1CRUdJTi4uLg==",
                "filename": "ca.crt",
                "auto_update_config": True,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
