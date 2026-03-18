"""Pydantic schemas for config-api requests and responses."""

import ipaddress
import re
from typing import Any

from pydantic import BaseModel, Field, field_serializer, field_validator

MAX_HOSTNAME_LENGTH = 253
MAX_LABEL_LENGTH = 63
HOSTNAME_LABEL_PATTERN = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$")


def validate_broker_address(address: str) -> tuple[bool, str | None]:
    if not address or not address.strip():
        return False, "Broker address cannot be empty"
    address = address.strip()

    try:
        ipaddress.ip_address(address)
        return True, None
    except ValueError:
        pass

    if address.startswith("[") and address.endswith("]"):
        try:
            ipaddress.IPv6Address(address[1:-1])
            return True, None
        except ValueError:
            pass

    if "." not in address and address.lower() != "localhost":
        return False, "Broker hostname must contain a dot or be 'localhost'"
    if address.isdigit():
        return False, "Broker hostname cannot be only digits"

    if len(address) > MAX_HOSTNAME_LENGTH:
        return False, f"Hostname too long: {len(address)} (max {MAX_HOSTNAME_LENGTH})"
    for label in address.split("."):
        if len(label) > MAX_LABEL_LENGTH:
            return False, f"Label '{label}' too long (max {MAX_LABEL_LENGTH})"
        if not label:
            return False, "Empty label in hostname"
        if not HOSTNAME_LABEL_PATTERN.match(label):
            return False, f"Invalid label format: '{label}'"
    return True, None


class BrokerConfigUpdate(BaseModel):
    broker: str | None = Field(default=None, min_length=1, max_length=253)
    broker_port: int | None = Field(default=None)
    mqtt_user: str | None = Field(default=None, min_length=1, max_length=128)
    mqtt_password: str | None = Field(default=None, min_length=1, max_length=256)
    mqtt_use_tls: bool | None = Field(default=None)
    mqtt_tls_insecure: bool | None = Field(default=None)
    auth_mode: str | None = Field(default=None, pattern="^(password|mtls|mtls_password)$")

    @field_validator("broker", mode="before")
    @classmethod
    def validate_broker(cls, v: Any) -> str | None:
        if v is None:
            return v
        if isinstance(v, str):
            v = v.strip()
            if not v:
                raise ValueError("Broker address cannot be empty")
            is_valid, error = validate_broker_address(v)
            if not is_valid:
                raise ValueError(error)
        return v

    @field_validator("broker_port", mode="before")
    @classmethod
    def validate_broker_port(cls, v: Any) -> int | None:
        if v is None:
            return v
        if isinstance(v, str):
            v = int(v)
        if isinstance(v, int) and (v < 1 or v > 65535):
            raise ValueError("broker_port must be between 1 and 65535")
        return v


class BrokerConfigResponse(BaseModel):
    broker: str
    broker_port: int = Field(ge=1, le=65535)
    mqtt_user: str = ""
    mqtt_password: str = ""
    mqtt_use_tls: bool = False
    mqtt_tls_insecure: bool = False
    auth_mode: str = "password"

    @field_serializer("mqtt_password", when_used="always")
    def serialize_password(self, value: str) -> str:
        return "***REDACTED***"


class CertificateUploadResponse(BaseModel):
    success: bool
    cert_type: str
    file_path: str
    filename: str
    message: str


class MultipleCertificatesUploadResponse(BaseModel):
    success: bool
    uploaded_files: dict[str, str]
    message: str


class CertificateBase64Upload(BaseModel):
    cert_type: str = Field(..., pattern="^(ca_cert|client_cert|client_key)$")
    content_base64: str = Field(..., max_length=1_000_000)
    filename: str | None = None
