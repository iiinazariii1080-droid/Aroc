from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from enum import Enum


class CameraMode(str, Enum):
    """Camera operation modes"""
    overlay = "overlay"
    stream = "stream"
    depth = "depth"


class OfferRequest(BaseModel):
    """Request for WebRTC offer creation"""
    sdp: str
    type: str
    color_index: int = 18
    stereo_index: int = 22
    mode: str = "overlay"


class DepthRequest(BaseModel):
    """Request for depth at point"""
    x: float
    y: float


class DepthResponse(BaseModel):
    """Response with depth data"""
    type: str = "depth"
    x: float
    y: float
    depth: float


class DepthErrorResponse(BaseModel):
    """Response for depth retrieval error"""
    type: str = "depth"
    error: str


class Point(BaseModel):
    """Point in polygon"""
    id: int
    name: str
    u: float  # 0.0 - 1.0
    v: float  # 0.0 - 1.0
    d: Optional[float] = None


class PolygonPayload(BaseModel):
    """Polygon data"""
    points: List[Point]
    polygon: List[int]


class CameraStatus(BaseModel):
    """Camera status"""
    connected: bool
    ip: str
    port: int
    last_ping: Optional[str] = None
    active_streams: int = 0
    mode: Optional[str] = None


class CameraConfig(BaseModel):
    """Camera configuration"""
    ip: str = "192.168.1.55"
    port: int = 9999
    timeout: int = 30
    max_streams: int = 10
    enable_depth: bool = True
    enable_overlay: bool = True
