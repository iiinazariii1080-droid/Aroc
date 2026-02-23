from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.events import register_event_handlers
from app.core.settings import get_settings
from app.routes import register_routes


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_title, version=settings.app_version)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

    register_routes(application)
    register_event_handlers(application)
    return application

