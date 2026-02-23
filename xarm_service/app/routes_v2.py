"""v2 Jobs API: async job create, status, cancel (API_EVOLUTION.md)."""
import asyncio
import uuid
from typing import Optional, Any, Dict
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.di import get_command_service
from drivers.xarm_driver.actor.commands import (
    Command,
    CommandType,
    ExecutionPolicy,
    ResultStatus,
)

router = APIRouter(prefix="/v2", tags=["v2 Jobs"])


class JobCreateRequest(BaseModel):
    command_id: str = Field(..., description="Idempotency key")
    type: str = Field(..., description="Command type: GET_STATUS, MOVE_JOINTS, MOVE_POSE, etc.")
    params: Dict[str, Any] = Field(default_factory=dict)
    policy: Optional[str] = Field("REJECT_IF_BUSY")
    timeout_ms: Optional[int] = None


class JobCreateResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    status: str  # queued | running | succeeded | failed | canceled
    progress: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# In-memory job store: job_id -> { status, result, error }
_jobs: Dict[str, Dict[str, Any]] = {}


def _type_from_str(s: str) -> CommandType:
    try:
        return CommandType(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown type: {s}")


def _policy_from_str(s: str) -> ExecutionPolicy:
    try:
        return ExecutionPolicy(s)
    except ValueError:
        return ExecutionPolicy.REJECT_IF_BUSY


async def _run_job(job_id: str, body: JobCreateRequest) -> None:
    svc = get_command_service()
    cmd = Command(
        command_id=body.command_id,
        type=_type_from_str(body.type),
        params=body.params,
        policy=_policy_from_str(body.policy or "REJECT_IF_BUSY"),
        timeout_s=(body.timeout_ms or 60000) / 1000.0,
    )
    _jobs[job_id]["status"] = "running"
    try:
        result = await svc.enqueue(cmd)
        _jobs[job_id]["result"] = {
            "command_id": result.command_id,
            "status": result.status.value,
            "error_code": result.error_code,
            "error_message": result.error_message,
            "telemetry_snapshot": result.telemetry_snapshot,
        }
        _jobs[job_id]["status"] = (
            "succeeded" if result.status == ResultStatus.SUCCEEDED else "failed"
        )
        if result.status == ResultStatus.FAILED:
            _jobs[job_id]["error"] = result.error_message
    except Exception as e:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(e)


@router.post("/jobs", response_model=JobCreateResponse)
async def create_job(body: JobCreateRequest):
    """Create a job; returns job_id immediately. Poll GET /v2/jobs/{job_id} for status/result."""
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "queued", "result": None, "error": None}
    asyncio.create_task(_run_job(job_id, body))
    return JobCreateResponse(job_id=job_id)


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Get job status, progress, result."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    j = _jobs[job_id]
    return JobStatusResponse(
        status=j["status"],
        progress=1.0 if j["status"] in ("succeeded", "failed", "canceled") else None,
        result=j.get("result"),
        error=j.get("error"),
    )


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Best-effort cancel: enqueue STOP."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if _jobs[job_id]["status"] not in ("queued", "running"):
        return {"status": _jobs[job_id]["status"]}
    svc = get_command_service()
    cmd = Command(
        command_id=str(uuid.uuid4()),
        type=CommandType.STOP,
        params={},
        policy=ExecutionPolicy.QUEUE,
    )
    await svc.enqueue(cmd)
    _jobs[job_id]["status"] = "canceled"
    return {"status": "canceled"}
