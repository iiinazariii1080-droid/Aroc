"""Task status endpoints."""
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Security, status
from fastapi import Path as PathParam
from pydantic import BaseModel

from app.core.security import Role, require_auth

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Bridge singleton accessor
# ---------------------------------------------------------------------------
_bridge_instance = None


def set_bridge_instance(bridge: Any) -> None:
    """Called from main.py after the bridge is created."""
    global _bridge_instance
    _bridge_instance = bridge


def _get_bridge() -> Any:
    """Return the bridge or None if not set."""
    return _bridge_instance


class TaskStatusResponse(BaseModel):
    """Response model for task status."""
    task_id: str
    command_id: str | None = None
    service: str
    request_id: str | None = None
    status: str  # "running", "completed", "failed", "timeout"
    started_at: str | None = None
    message: str | None = None
    note: str = "Task status is tracked via MQTT topics. Use MQTT client to subscribe to status topics for real-time updates."


@router.get(
    "/tasks/{task_id}/status",
    response_model=TaskStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get task status",
    description="""
    Get status of a task by task_id.

    **Note:** This endpoint provides basic task information. For real-time updates,
    subscribe to MQTT status topics:
    - `aroc/robot/{robot_id}/status/navigation` - navigation command status
    - `aroc/robot/{robot_id}/status/system` - system status with active tasks

    **Task Status Values:**
    - `running`: Task is currently executing
    - `completed`: Task completed successfully
    - `failed`: Task failed with error
    - `timeout`: Task timed out

    **MQTT Real-time Tracking:**
    For real-time task status updates, subscribe to MQTT topics:
    ```python
    # Subscribe to navigation status
    client.subscribe("aroc/robot/{robot_id}/status/navigation")

    # Subscribe to system status (includes active tasks list)
    client.subscribe("aroc/robot/{robot_id}/status/system")
    ```
    """,
    responses={
        200: {
            "description": "Task status retrieved",
        },
        404: {
            "description": "Task not found",
        },
        401: {
            "description": "Unauthorized - missing or invalid API key",
        },
    }
)
async def get_task_status(
    task_id: str = PathParam(..., min_length=1, max_length=256, pattern=r"^[a-zA-Z0-9_\-]+$"),
    role: Role = Security(require_auth(Role.READ))
) -> TaskStatusResponse:
    """
    Get status of a task.

    **Authentication:**
    - Requires API key with READ role or higher

    **Note:** This is a basic status endpoint. For real-time updates,
    use MQTT subscriptions to status topics.

    **Example request:**
    ```bash
    curl -X GET "http://localhost:7900/api/v1/tasks/task-123/status" \
      -H "X-API-Key: your-api-key"
    ```

    **Example response:**
    ```json
    {
      "task_id": "task-123",
      "command_id": "cmd-456",
      "service": "robot",
      "request_id": "req-789",
      "status": "running",
      "started_at": "2024-01-01T12:00:00Z",
      "message": "Task is executing",
      "note": "Task status is tracked via MQTT topics..."
    }
    ```
    """
    bridge = _get_bridge()
    if bridge is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bridge component is not available.",
        )

    task_info = bridge.get_task_info(task_id)

    if task_info is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found among active tasks.",
        )

    return TaskStatusResponse(
        task_id=task_id,
        command_id=task_info.command_id,
        service=task_info.service,
        request_id=task_info.request_id,
        status=task_info.status,
        started_at=datetime.fromtimestamp(task_info.started_at, tz=UTC).isoformat(),
        message=None,
    )


@router.get(
    "/tasks",
    summary="List active tasks",
    description="""
    Get list of active tasks.

    **Note:** This endpoint provides basic information. For real-time updates,
    subscribe to MQTT status topics.
    """,
    responses={
        200: {
            "description": "List of active tasks",
        },
        401: {
            "description": "Unauthorized - missing or invalid API key",
        },
    }
)
async def list_tasks(
    role: Role = Security(require_auth(Role.READ))
) -> dict:
    """
    List active tasks.

    **Authentication:**
    - Requires API key with READ role or higher

    **Note:** Bridge runs in a separate process. For real-time task information,
    subscribe to MQTT topic: `aroc/robot/{robot_id}/status/system`
    """
    bridge = _get_bridge()
    if bridge is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bridge component is not available.",
        )

    snapshot = bridge.get_active_tasks()

    tasks = []
    for tid, info in snapshot.items():
        tasks.append({
            "task_id": tid,
            "service": info.service,
            "status": info.status,
            "started_at": datetime.fromtimestamp(info.started_at, tz=UTC).isoformat(),
        })

    return {"active_tasks": tasks, "count": len(tasks)}

