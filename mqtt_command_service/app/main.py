"""FastAPI application main module."""
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.exception_handlers import (
    base_api_exception_handler,
    general_exception_handler,
    validation_exception_handler,
)
from app.core.exceptions import BaseAPIException

# Configure logging
from app.core.logging_config import setup_logging
from app.middleware.logging import log_requests_middleware
from app.middleware.metrics import setup_prometheus_metrics
from app.middleware.rate_limit import setup_rate_limiting
from app.middleware.security_headers import add_security_headers_middleware
from app.middleware.version import add_api_version_middleware
from app.utils.file_utils import cleanup_old_temp_files

setup_logging(
    log_level=settings.LOG_LEVEL,
    json_format=settings.JSON_LOGS,
    log_file=settings.LOG_FILE
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup / shutdown."""
    logger.info("Starting %s v%s", settings.API_TITLE, settings.API_VERSION)
    logger.info("Certificate storage: %s", settings.CERT_STORAGE_DIR)

    # Cleanup old temporary files
    deleted = cleanup_old_temp_files()
    if deleted > 0:
        logger.info("Cleaned up %d old temporary file(s)", deleted)

    yield  # app is running

    logger.info("Shutting down application")


# Create FastAPI app
app = FastAPI(
    title=settings.API_TITLE,
    description=settings.API_DESCRIPTION,
    version=settings.API_VERSION,
    lifespan=lifespan,
)

# Setup rate limiting (disabled by default; enable with RATE_LIMIT_ENABLED=true)
if settings.RATE_LIMIT_ENABLED:
    setup_rate_limiting(app)

# CORS — deny all cross-origin requests by default (API-only service).
# Override via CORS_ORIGINS env var if browser clients are needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],  # No origins allowed by default
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["X-API-Key", "Content-Type"],
)

# Setup Prometheus metrics
setup_prometheus_metrics(app)

# Add exception handlers
app.add_exception_handler(BaseAPIException, base_api_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(Exception, general_exception_handler)

# Add middleware (order: last added = first executed)
app.middleware("http")(add_security_headers_middleware)  # Security headers
app.middleware("http")(log_requests_middleware)
app.middleware("http")(add_api_version_middleware)  # Add version header

# Include routers (single canonical prefix)
app.include_router(api_router, prefix="/api/v1")

