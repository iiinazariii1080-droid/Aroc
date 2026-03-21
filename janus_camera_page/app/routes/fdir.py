"""FDIR diagnostics API routes.

Exposes the recovery ladder status, FDIR event log, and system mode
for operator diagnostics and automated monitoring.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.admin import require_admin
from app.middleware.rate_limit import require_admin_rate_limit
from app.services import system_mode
from app.services.fdir_events import recent as fdir_recent
from app.services.recovery_ladder import get_ladder

router = APIRouter(prefix="/fdir", tags=["fdir"])
ADMIN_DEPENDENCY = Depends(require_admin)
ADMIN_RATE_LIMIT = Depends(require_admin_rate_limit)


class LadderStatus(BaseModel):
    current_level: int
    current_level_name: str
    total_recoveries: int
    levels: List[Dict[str, Any]]


class ModeInfo(BaseModel):
    mode: str
    since: float
    uptime_s: float
    reason: str
    policy: Dict[str, Any]


@router.get("/ladder", response_model=LadderStatus, summary="Recovery ladder status", dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT])
def ladder_status() -> LadderStatus:
    data = get_ladder().status()
    return LadderStatus(**data)


@router.get("/events", summary="Recent FDIR events (newest first)", dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT])
def fdir_events(n: int = 50) -> List[Dict[str, Any]]:
    return fdir_recent(n)


@router.get("/mode", response_model=ModeInfo, summary="Current system operating mode", dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT])
def mode_status() -> ModeInfo:
    data = system_mode.mode_info()
    return ModeInfo(**data)


@router.post("/mode/{target}", summary="Force system mode transition (admin)", dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT])
def force_mode(target: str, reason: str = "manual_override") -> Dict[str, Any]:
    try:
        mode = system_mode.SystemMode(target)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"unknown mode: {target}, valid: {[m.value for m in system_mode.SystemMode]}",
        )
    ok = system_mode.transition(mode, reason)
    return {"transitioned": ok, "current": system_mode.current_mode().value}


@router.post("/ladder/reset", summary="Reset recovery ladder to level 0", dependencies=[ADMIN_DEPENDENCY, ADMIN_RATE_LIMIT])
def reset_ladder() -> Dict[str, Any]:
    get_ladder().reset()
    return {"reset": True, "status": get_ladder().status()}
