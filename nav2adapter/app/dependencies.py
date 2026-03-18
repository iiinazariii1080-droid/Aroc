"""
Dependency injection for FastAPI.
"""
import hmac
from typing import Annotated, AsyncIterator, Optional
from fastapi import Depends, Request, HTTPException, Security
from fastapi.security import APIKeyHeader
from services.symovo_service import SymovoAgvClient
from services.event_bus import EventBus
from services.event_stream_service import EventStreamService
from services.state_store import StateStore
from services.status_publisher import StatusPublisher
from services.safety_state_tracker import SafetyStateTracker
from services.command_handler import CommandHandler

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_command_auth(
    api_key: Optional[str] = Security(_api_key_header),
) -> None:
    """Validate X-API-Key header for mutation endpoints.

    Three modes:
    1. COMMAND_API_KEY_DISABLED=true → no auth (explicit opt-out).
    2. COMMAND_API_KEY set → require matching X-API-Key header.
    3. Neither set → reject with 500 (misconfiguration).
    """
    from app.config import settings
    if getattr(settings, 'command_api_key_disabled', False):
        return  # explicit opt-out
    expected = getattr(settings, 'command_api_key', None)
    if not expected:
        raise HTTPException(
            status_code=500,
            detail="Auth not configured: set COMMAND_API_KEY or COMMAND_API_KEY_DISABLED=true",
        )
    if not api_key or not hmac.compare_digest(api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


async def get_symovo_client(request: Request) -> AsyncIterator[SymovoAgvClient]:
    """Get the shared Symovo AGV client instance."""
    # Prefer a shared client created during app startup (stable cache, fewer sockets).
    shared = getattr(request.app.state, "symovo_client", None)
    if isinstance(shared, SymovoAgvClient):
        yield shared
        return

    # Fallback to per-request client if startup didn't run (e.g. tests).
    client = SymovoAgvClient()
    try:
        yield client
    finally:
        await client.close()


def get_state_store(request: Request):
    """Get StateStore from app services container."""
    svc = getattr(request.app.state, "services", None)
    if svc is not None:
        return svc.state_store
    raise HTTPException(status_code=503, detail="StateStore not available")


def get_event_bus(request: Request) -> EventBus:
    """Get EventBus from app services container."""
    svc = getattr(request.app.state, "services", None)
    bus = getattr(svc, "event_bus", None) if svc is not None else None
    if bus is not None:
        return bus
    raise HTTPException(status_code=503, detail="EventBus not available")


def get_event_stream(request: Request) -> EventStreamService:
    """Get EventStreamService from app services container."""
    svc = getattr(request.app.state, "services", None)
    stream = getattr(svc, "event_stream", None) if svc is not None else None
    if stream is not None:
        return stream
    raise HTTPException(status_code=503, detail="EventStreamService not available")


def get_command_handler(request: Request) -> CommandHandler:
    """Get CommandHandler from app services container."""
    svc = getattr(request.app.state, "services", None)
    handler = getattr(svc, "command_handler", None) if svc is not None else None
    if handler is not None:
        return handler
    raise HTTPException(status_code=503, detail="CommandHandler not available")


def get_safety_tracker(request: Request) -> SafetyStateTracker:
    """Get shared SafetyStateTracker from app state."""
    tracker = getattr(request.app.state, "safety_tracker", None)
    if tracker is not None:
        return tracker
    raise HTTPException(status_code=503, detail="SafetyStateTracker not available")


def get_status_publisher(request: Request) -> StatusPublisher:
    """Get StatusPublisher from app services container."""
    svc = getattr(request.app.state, "services", None)
    pub = getattr(svc, "status_publisher", None) if svc is not None else None
    if pub is not None:
        return pub
    raise HTTPException(status_code=503, detail="StatusPublisher not available")


# Type aliases for dependency injection
SymovoClient = Annotated[SymovoAgvClient, Depends(get_symovo_client)]
InjectedStateStore = Annotated[StateStore, Depends(get_state_store)]
InjectedEventBus = Annotated[EventBus, Depends(get_event_bus)]
InjectedEventStream = Annotated[EventStreamService, Depends(get_event_stream)]
InjectedCommandHandler = Annotated[CommandHandler, Depends(get_command_handler)]
InjectedSafetyTracker = Annotated[SafetyStateTracker, Depends(get_safety_tracker)]
