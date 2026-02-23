"""
Navigation progress computation utilities.

We compute progress as straight-line distance between (start -> goal), using current pose updates.
Progress is monotonic: it never decreases (we track the minimum remaining distance observed).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from domain.models import Pose2D, NavigationSession


def _now() -> datetime:
    return datetime.now(timezone.utc)


def dist_m(a: Pose2D, b: Pose2D) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def update_session_from_current(
    session: NavigationSession,
    current: Pose2D,
    *,
    eps_m: float = 0.05,
    clamp_max_while_active: int = 99,
) -> NavigationSession:
    """
    Update session progress with a new current pose snapshot.

    - eps_m avoids division by tiny values when start ~ goal.
    - clamp_max_while_active prevents showing 100% before we get a terminal transport state.
    """
    total = float(session.total_dist_m or 0.0)
    remaining = dist_m(current, session.goal)

    if total <= eps_m:
        # Degenerate: start ~ goal. Start with small progress (1-5%) instead of jumping to 99%.
        # This allows the progress to increase naturally as the robot moves away and back.
        # Check if robot is at start position (within small threshold)
        dist_from_start = dist_m(current, session.start)
        at_start = dist_from_start <= eps_m
        
        # If very close to goal AND closer to goal than to start, set high progress
        # This handles the case where start and goal are very close but robot is at goal
        closer_to_goal = remaining < dist_from_start
        if remaining <= eps_m and (closer_to_goal or not at_start):
            # At goal (and not at start), set high progress but not 100% until terminal state
            session.progress_percent = max(session.progress_percent, int(clamp_max_while_active))
            session.min_remaining_dist_m = 0.0
        elif at_start:
            # Start and goal are close
            # If at start position, progress should be minimal (1%)
            # If moved away from start towards goal, progress increases
            start_to_goal = total
            current_to_goal = remaining
            
            # Ensure min_remaining_dist_m is initialized correctly (not 0.0 if we haven't reached goal)
            current_min = float(session.min_remaining_dist_m or total)
            # Don't allow min_remaining to be 0.0 if we're not at goal
            if current_min <= eps_m and remaining > eps_m:
                # Reset to total if it was incorrectly set to 0
                current_min = total
            
            # Update min_remaining to track the minimum distance we've seen
            current_min = min(current_min, remaining)
            session.min_remaining_dist_m = current_min
            
            # Calculate progress: if we're at or near start, progress is minimal
            # If we've moved closer to goal, progress increases
            if at_start or current_to_goal >= start_to_goal - 0.001:  # At or near start position
                # At start: minimal progress (1%)
                initial_progress = max(1, session.progress_percent or 0)
            else:
                # We've made progress: calculate based on distance covered
                progress_ratio = 1.0 - (current_min / max(start_to_goal, eps_m))
                initial_progress = max(1, min(int(progress_ratio * clamp_max_while_active), clamp_max_while_active))
            
            session.progress_percent = max(session.progress_percent or 0, initial_progress)
        session.updated_at = _now()
        return session

    min_remaining = min(float(session.min_remaining_dist_m or total), remaining)
    ratio = 1.0 - (min_remaining / total)
    pct = int(round(100.0 * max(0.0, min(1.0, ratio))))
    pct = max(0, min(int(clamp_max_while_active), pct))

    session.min_remaining_dist_m = float(min_remaining)
    session.progress_percent = max(int(session.progress_percent or 0), pct)
    session.updated_at = _now()
    return session

