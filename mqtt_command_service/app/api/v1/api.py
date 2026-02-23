"""API v1 router aggregation."""
from fastapi import APIRouter

from app.api.v1.endpoints import auth, broker, certificates, health, tasks

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(broker.router, tags=["config"])
api_router.include_router(certificates.router, tags=["config"])
api_router.include_router(tasks.router, tags=["tasks"])

