from fastapi import FastAPI

from app.routes import camera, fdir, janus, metrics, system, telemetry


def register_routes(app: FastAPI) -> None:
    app.include_router(system.router)
    app.include_router(camera.router)
    app.include_router(janus.router)
    app.include_router(fdir.router)
    app.include_router(metrics.router)
    app.include_router(telemetry.router)

