"""
Robot positions CRUD endpoints.

Extracted from main.py (НАР-7) to reduce main module size.
"""
import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, ConfigDict, field_validator

from db.robot_positions import get_robot_positions_list, save_robot_position, delete_robot_position
from models.api_types import PositionParams

router = APIRouter(prefix="/robot_positions", tags=["robot_positions"])


class RobotPositionSaveRequest(BaseModel):
    """Schema for POST /robot_positions/save."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, description="Position id for update (omit for create)")
    name: str = Field(..., min_length=1, description="Unique non-empty position name")
    params: PositionParams = Field(..., description="Position params with required location")

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must be non-empty")
        return v


@router.get("/list")
def api_get_robot_positions_list():
    result = get_robot_positions_list()
    return result


@router.post("/save", status_code=status.HTTP_201_CREATED)
def api_save_robot_position(req: RobotPositionSaveRequest):
    try:
        position_id = req.id if req.id else uuid.uuid4().hex[:8]

        save_robot_position({
            "id": position_id,
            "name": req.name,
            "params": req.params.model_dump(exclude_none=True),
        })
        return {"status": "ok", "message": "Robot position saved.", "id": position_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{position_id}", status_code=status.HTTP_200_OK)
def api_delete_robot_position(position_id: str):
    try:
        delete_robot_position(position_id)
        return {"status": "ok", "message": "Robot position deleted."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
