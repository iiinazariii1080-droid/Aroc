import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def safe_json_loads(payload: str) -> tuple[Any, str | None]:
    """
    Safely parse JSON payload with error reporting.

    Returns:
        Tuple of (parsed_data, error_message):
        - (parsed_data, None) if successful
        - (None, error_message) if parsing failed
    """
    try:
        return json.loads(payload), None
    except json.JSONDecodeError as e:
        error_msg = f"JSON decode error at line {e.lineno}, column {e.colno}: {e.msg}"
        logger.debug("JSON parsing failed: %s", error_msg)
        return None, error_msg
    except Exception as e:
        error_msg = f"Unexpected error parsing JSON: {e!s}"
        logger.warning("Unexpected JSON parsing error: %s", error_msg, exc_info=True)
        return None, error_msg


def is_task_done(body: Any) -> bool:
    """
    Simple heuristic to determine if a task is done based on status/details.
    """
    if not isinstance(body, dict):
        return True

    detail = body.get("detail")
    if detail is not None:
        detail_str = str(detail).lower()
        if detail_str and detail_str != "none" and detail_str != "working":
            return True

    state = str(body.get("state", "") or body.get("status", "")).lower()
    from constants import TASK_TERMINAL_STATES
    if state in TASK_TERMINAL_STATES:
        return True

    return body.get("success") is False

