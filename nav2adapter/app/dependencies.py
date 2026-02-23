"""
Dependency injection для FastAPI.
"""
from typing import Annotated, AsyncIterator
from fastapi import Depends, Request
from services.symovo_service import SymovoAgvClient


async def get_symovo_client(request: Request) -> AsyncIterator[SymovoAgvClient]:
    """Получить экземпляр Symovo AGV клиента."""
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


# Тип для dependency injection
SymovoClient = Annotated[SymovoAgvClient, Depends(get_symovo_client)]
