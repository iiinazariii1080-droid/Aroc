"""Prometheus /metrics endpoint."""

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

router = APIRouter(tags=["Metrics"])


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
