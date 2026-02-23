from fastapi import FastAPI

from app.routes import camera, janus, system


def register_routes(app: FastAPI) -> None:
    app.include_router(system.router)
    app.include_router(camera.router)
    app.include_router(janus.router)

