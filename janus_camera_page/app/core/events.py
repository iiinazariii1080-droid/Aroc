from fastapi import FastAPI

from app.services import janus_proxy, relay_proxy, watchdogs


def register_event_handlers(app: FastAPI) -> None:
    @app.on_event("startup")
    async def _startup() -> None:
        watchdogs.start_janus_watchdog()
        await watchdogs.start_snapshot_watchdog()
        await janus_proxy.start_client()
        await relay_proxy.start_client()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await janus_proxy.stop_client()
        await relay_proxy.stop_client()

