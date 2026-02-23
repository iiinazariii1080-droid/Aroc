import asyncio
import aiohttp
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from app.models.camera import (
    CameraConfig, CameraStatus, OfferRequest, 
    DepthRequest, DepthResponse, DepthErrorResponse
)

logger = logging.getLogger(__name__)


class CameraService:
    """Service for working with WebRTC camera"""
    
    def __init__(self, config: CameraConfig):
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None
        self.active_streams = 0
        self.last_ping: Optional[datetime] = None
        self._connection_status = False
        
    async def __aenter__(self):
        """Async context manager - enter"""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config.timeout)
        )
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager - exit"""
        if self.session:
            await self.session.close()
            
    async def check_connection(self) -> bool:
        """Check camera connection"""
        try:
            if not self.session:
                return False
                
            url = f"http://{self.config.ip}:{self.config.port}/"
            async with self.session.get(url) as response:
                self._connection_status = response.status == 200
                if self._connection_status:
                    self.last_ping = datetime.now()
                return self._connection_status
                
        except Exception as e:
            logger.error(f"Camera connection check error: {e}")
            self._connection_status = False
            return False
            
    async def get_status(self) -> CameraStatus:
        """Get camera status"""
        connected = await self.check_connection()
        return CameraStatus(
            connected=connected,
            ip=self.config.ip,
            port=self.config.port,
            last_ping=self.last_ping.isoformat() if self.last_ping else None,
            active_streams=self.active_streams
        )
        
    async def create_webrtc_offer(self, offer_data: OfferRequest) -> Dict[str, Any]:
        """Create WebRTC offer for camera"""
        try:
            if not self.session:
                raise Exception("Сессия не инициализирована")
                
            url = f"http://{self.config.ip}:{self.config.port}/offer"
            payload = offer_data.dict()
            
            async with self.session.post(url, json=payload) as response:
                if response.status != 200:
                    try:
                        body_text = await response.text()
                    except Exception:
                        body_text = "<no body>"
                    raise Exception(f"Camera error {response.status} at {url}: {body_text}")
                result = await response.json()
                self.active_streams += 1
                return result
                
        except Exception as e:
            logger.error(f"WebRTC offer creation error: {e}")
            raise
            
    async def create_overlay_offer(self, offer_data: OfferRequest) -> Dict[str, Any]:
        """Create overlay offer for camera"""
        try:
            if not self.session:
                raise Exception("Сессия не инициализирована")
                
            url = f"http://{self.config.ip}:{self.config.port}/overlay_offer"
            payload = offer_data.dict()
            
            async with self.session.post(url, json=payload) as response:
                if response.status != 200:
                    try:
                        body_text = await response.text()
                    except Exception:
                        body_text = "<no body>"
                    raise Exception(f"Camera error {response.status} at {url}: {body_text}")
                result = await response.json()
                self.active_streams += 1
                return result
                
        except Exception as e:
            logger.error(f"Overlay offer creation error: {e}")
            raise
            
    async def get_depth(self, depth_request: DepthRequest) -> DepthResponse:
        """Get depth at specified point"""
        try:
            if not self.session:
                raise Exception("Session not initialized")
                
            url = f"http://{self.config.ip}:{self.config.port}/depth"
            # Convert from 0.0-1.0 to 0-100 for camera
            x_camera = depth_request.x * 100
            y_camera = depth_request.y * 100
            params = {
                "message": json.dumps({"x": x_camera, "y": y_camera})
            }
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return DepthResponse(**data)
                elif response.status == 400:
                    # Try to extract JSON error first, then fallback to text
                    try:
                        error_data = await response.json()
                        error_msg = error_data.get("error") or error_data.get("detail") or "Depth retrieval error"
                    except Exception:
                        error_msg = await response.text()
                    raise Exception(f"Camera 400: {error_msg}")
                else:
                    # Include body for diagnostics
                    try:
                        body = await response.text()
                    except Exception:
                        body = "<no body>"
                    raise Exception(f"Camera error: {response.status} - {body}")
                    
        except Exception as e:
            logger.error(f"Depth retrieval error: {e}")
            raise
            
    async def submit_polygon(self, polygon_data: Dict[str, Any]) -> Dict[str, Any]:
        """Submit polygon to camera"""
        try:
            if not self.session:
                raise Exception("Сессия не инициализирована")
                
            url = f"http://{self.config.ip}:{self.config.port}/polygon"
            
            async with self.session.post(url, json=polygon_data) as response:
                if response.status != 200:
                    raise Exception(f"Ошибка камеры: {response.status}")
                    
                return await response.json()
                
        except Exception as e:
            logger.error(f"Polygon submission error: {e}")
            raise
            
    def close_stream(self):
        """Close stream"""
        if self.active_streams > 0:
            self.active_streams -= 1
