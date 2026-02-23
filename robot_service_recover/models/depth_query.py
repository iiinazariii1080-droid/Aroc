# app/models/depth_query.py
from pydantic import BaseModel

class DepthQuery(BaseModel):
    x: int
    y: int
