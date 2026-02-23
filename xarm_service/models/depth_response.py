# app/models/depth_response.py
from pydantic import BaseModel

class DepthResponse(BaseModel):
    depth: float
    error: str = None
