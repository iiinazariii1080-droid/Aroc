from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import HTMLResponse
from typing import Dict, Any
import logging
import os

from app.models.camera import (
    OfferRequest, DepthRequest, DepthResponse, DepthErrorResponse,
    PolygonPayload, CameraStatus, CameraConfig
)
from app.services.camera_service import CameraService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["camera"])

# Global camera configuration
camera_config = CameraConfig(
    ip=os.getenv("CAMERA_IP", "192.168.1.55"),
    port=int(os.getenv("CAMERA_PORT", "9999")),
    timeout=30,
    max_streams=10,
    enable_depth=True,
    enable_overlay=True
)

# Global camera service instance
camera_service: CameraService = None


async def get_camera_service() -> CameraService:
    """Dependency for camera service"""
    global camera_service
    if camera_service is None:
        camera_service = CameraService(camera_config)
        await camera_service.__aenter__()
    return camera_service


@router.post("/depth_camera/overlay_offer")
async def create_overlay_offer(
    offer_data: OfferRequest,
    service: CameraService = Depends(get_camera_service)
) -> Dict[str, Any]:
    """Create overlay offer"""
    try:
        return await service.create_overlay_offer(offer_data)
    except Exception as e:
        logger.error(f"Overlay offer creation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/depth_camera/depth", response_model=DepthResponse)
async def get_depth(
    message: str,
    service: CameraService = Depends(get_camera_service)
) -> DepthResponse:
    """Get depth at specified point"""
    try:
        import json
        coords = json.loads(message)
        # Store original coordinates for response
        x_original = coords["x"]
        y_original = coords["y"]
        # Convert from 0-100 coordinates to 0.0-1.0 for camera service
        x_normalized = coords["x"] / 100.0
        y_normalized = coords["y"] / 100.0
        depth_request = DepthRequest(x=x_normalized, y=y_normalized)
        depth_result = await service.get_depth(depth_request)
        
        # Check if camera returned coordinates in 0-100 format
        if hasattr(depth_result, 'x') and depth_result.x <= 100:
            # Camera returned 0-100 coordinates, return as is
            return depth_result
        else:
            # Return response with original coordinates (0-100)
            return DepthResponse(
                type=depth_result.type,
                x=x_original,
                y=y_original,
                depth=depth_result.depth
            )
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON format")
    except Exception as e:
        logger.error(f"Depth retrieval error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
