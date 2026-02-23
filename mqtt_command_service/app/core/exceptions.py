"""Custom exceptions for the application."""

from fastapi import HTTPException, status


class BaseAPIException(HTTPException):
    """Base exception for API errors."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        error_code: str | None = None,
        headers: dict | None = None
    ):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.error_code = error_code or self.__class__.__name__


class ValidationError(BaseAPIException):
    """Validation error (400)."""

    def __init__(self, detail: str, error_code: str = "VALIDATION_ERROR"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
            error_code=error_code
        )


class ConfigurationError(BaseAPIException):
    """Configuration error (500)."""

    def __init__(self, detail: str, error_code: str = "CONFIGURATION_ERROR"):
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail,
            error_code=error_code
        )


class CertificateError(BaseAPIException):
    """Certificate-related error (400)."""

    def __init__(self, detail: str, error_code: str = "CERTIFICATE_ERROR"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
            error_code=error_code
        )


class DatabaseError(BaseAPIException):
    """Database error (500)."""

    def __init__(self, detail: str, error_code: str = "DATABASE_ERROR"):
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail,
            error_code=error_code
        )

