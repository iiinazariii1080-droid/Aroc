from fastapi import APIRouter
from app.routes.camera import router as camera_router

router = APIRouter()
router.include_router(camera_router)