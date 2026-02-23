"""Pydantic schemas for API requests and responses."""
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.utils.validation import validate_broker_address
from constants import MQTT_DEFAULT_PORT, MQTT_TLS_PORT


class BrokerConfigUpdate(BaseModel):
    """Model for updating MQTT broker configuration."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "MQTT_BROKER": "123.123.123.123",
                "MQTT_PORT": MQTT_DEFAULT_PORT,
                "mqtt_user": "myuser",
                "mqtt_password": "mypassword",
                "mqtt_use_tls": False,
                "mqtt_ca_certs": None,
                "mqtt_certfile": None,
                "mqtt_keyfile": None,
                "mqtt_tls_insecure": False
            }
        }
    )

    broker: str | None = Field(
        default=None,
        description="MQTT broker address (IP address or hostname)",
        examples=["192.168.1.100", "broker.example.com", "localhost"],
        min_length=1,
        max_length=253,
    )

    broker_port: int | None = Field(
        default=None,
        description="MQTT broker port",
        examples=[MQTT_DEFAULT_PORT, MQTT_TLS_PORT, 1884],
    )

    mqtt_user: str | None = Field(
        default=None,
        description="MQTT authentication username (leave empty for anonymous connection)",
        examples=["admin", "user", "mqtt_user"],
        min_length=1,
        max_length=128,
    )

    mqtt_password: str | None = Field(
        default=None,
        description="MQTT authentication password",
        examples=["password123", "secret"],
        min_length=1,
        max_length=256,
    )

    mqtt_use_tls: bool | None = Field(
        default=None,
        description="Enable TLS/SSL encryption for MQTT connection",
        examples=[True, False],
    )

    # Certificate paths are fixed and cannot be changed via API
    # Certificates are always stored in 'certs/' directory in application root
    # Use /api/v1/config/certificates/upload to upload certificates

    mqtt_tls_insecure: bool | None = Field(
        default=None,
        description="Disable certificate verification (WARNING: only for dev/testing!)",
        examples=[False],
    )

    @field_validator("broker", mode='before')
    @classmethod
    def validate_broker(cls, v: Any) -> str | None:
        """Validate broker address (IP or hostname)."""
        if v is None:
            return v

        if not isinstance(v, str):
            return v  # type: ignore[no-any-return]  # pydantic handles type coercion

        v = v.strip()
        if not v:
            raise ValueError("Broker address cannot be empty")

        # Use improved validation utility
        is_valid, error_message = validate_broker_address(v)
        if not is_valid:
            raise ValueError(error_message)

        return v  # type: ignore[no-any-return]  # v is str after strip()

    @field_validator("broker_port", mode='before')
    @classmethod
    def validate_broker_port(cls, v: Any) -> int | None:
        """Validate broker port."""
        if v is None:
            return v

        # Convert to int if needed
        if isinstance(v, str):
            try:
                v = int(v)
            except ValueError:
                raise ValueError("broker_port must be an integer")

        if not isinstance(v, int):
            return v  # type: ignore[no-any-return]  # pydantic handles type coercion

        if v < 1 or v > 65535:
            raise ValueError("broker_port must be between 1 and 65535")

        return v

    @field_validator("mqtt_user")
    @classmethod
    def validate_mqtt_user(cls, v: str | None) -> str | None:
        """Validate MQTT username."""
        if v is None:
            return v

        v = v.strip()
        # Allow empty string for anonymous connections

        # Check that it doesn't contain invalid characters
        # MQTT usually allows any UTF-8 characters, but it's better to limit
        if len(v.encode('utf-8')) > 128:
            raise ValueError("MQTT username is too long (max 128 bytes)")

        return v

    @field_validator("mqtt_password")
    @classmethod
    def validate_mqtt_password(cls, v: str | None) -> str | None:
        """Validate MQTT password."""
        if v is None:
            return v

        # Password can be empty for some brokers
        if len(v.encode('utf-8')) > 256:
            raise ValueError("MQTT password is too long (max 256 bytes)")

        return v

    # Certificate path validation removed - paths are now fixed
    # Certificates are always stored in 'certs/' directory


class BrokerConfigResponse(BaseModel):
    """Response model with MQTT broker settings."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "MQTT_BROKER": "123.123.123.123,
                "MQTT_PORT": MQTT_DEFAULT_PORT,
                "mqtt_user": "admin",
                "mqtt_password": "secret",
                "mqtt_use_tls": False,
                "mqtt_tls_insecure": False
            }
        }
    )

    broker: str = Field(
        description="MQTT broker address",
        examples=["192.168.1.100", "broker.example.com"],
    )

    broker_port: int = Field(
        description="MQTT broker port",
        examples=[MQTT_DEFAULT_PORT, MQTT_TLS_PORT],
        ge=1,
        le=65535,
    )

    mqtt_user: str = Field(
        default="",
        description="MQTT username",
        examples=["admin", "user"],
    )

    mqtt_password: str = Field(
        default="",
        description="MQTT password (hidden in logs)",
        examples=["********"],
    )

    mqtt_use_tls: bool = Field(
        description="TLS/SSL encryption enabled",
        examples=[True, False],
    )

    mqtt_tls_insecure: bool = Field(
        description="Certificate verification disabled (WARNING: only for dev!)",
        examples=[False],
    )

    @field_serializer("mqtt_password", when_used="json")
    def serialize_password(self, value: str) -> str:
        """Hide password during serialization for security."""
        return "***REDACTED***"


class CertificateBase64Upload(BaseModel):
    """Model for uploading certificate via base64 (for MQTT)."""
    cert_type: str = Field(
        ...,
        description="Type of certificate: 'ca_cert', 'client_cert', or 'client_key'",
        pattern="^(ca_cert|client_cert|client_key)$"
    )
    content_base64: str = Field(
        ...,
        description="Base64-encoded certificate content",
        max_length=1_000_000,
    )
    filename: str | None = Field(
        default=None,
        description="Optional filename (auto-generated if not provided)"
    )
    auto_update_config: bool = Field(
        default=True,
        description="Automatically update configuration"
    )


class ConnectionTestResult(BaseModel):
    """Result of connection test."""
    success: bool = Field(description="Whether connection was successful")
    error: str | None = Field(
        default=None,
        description="Error message if connection failed"
    )


class CertificateUploadResponse(BaseModel):
    """Response model for certificate upload."""
    success: bool = Field(description="Whether upload was successful")
    cert_type: str = Field(
        description="Type of certificate",
        pattern="^(ca_cert|client_cert|client_key)$"
    )
    file_path: str = Field(description="Path where certificate was saved")
    filename: str = Field(description="Filename of uploaded certificate")
    auto_updated_config: bool = Field(
        description="Whether configuration was automatically updated"
    )
    message: str = Field(description="Human-readable message")


class MultipleCertificatesUploadResponse(BaseModel):
    """Response model for multiple certificates upload."""
    success: bool = Field(description="Whether all uploads were successful")
    uploaded_files: dict[str, str] = Field(
        description="Dictionary of cert_type -> file_path for uploaded files"
    )
    auto_updated_config: bool = Field(
        description="Whether configuration was automatically updated"
    )
    message: str = Field(description="Human-readable message")
