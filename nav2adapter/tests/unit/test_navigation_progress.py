import pytest

from domain.models import NavigationSession, Pose2D
from services.navigation_progress import update_session_from_current, dist_m


def test_progress_monotonic_increases():
    start = Pose2D(x=0.0, y=0.0, map_id=0)
    goal = Pose2D(x=10.0, y=0.0, map_id=0)
    total = dist_m(start, goal)
    s = NavigationSession(
        command_id="c1",
        target_id="P1",
        start=start,
        goal=goal,
        total_dist_m=total,
        min_remaining_dist_m=total,
        progress_percent=0,
    )

    # Move forward: progress should increase
    s = update_session_from_current(s, Pose2D(x=1.0, y=0.0, map_id=0))
    p1 = s.progress_percent
    s = update_session_from_current(s, Pose2D(x=5.0, y=0.0, map_id=0))
    p2 = s.progress_percent
    assert p2 >= p1

    # Move backwards: raw distance would decrease progress, but monotonic clamp must hold
    s = update_session_from_current(s, Pose2D(x=2.0, y=0.0, map_id=0))
    assert s.progress_percent == p2


def test_progress_degenerate_start_equals_goal_clamps_under_100():
    start = Pose2D(x=1.0, y=2.0, map_id=0)
    goal = Pose2D(x=1.0, y=2.0, map_id=0)
    s = NavigationSession(
        command_id="c2",
        target_id="P2",
        start=start,
        goal=goal,
        total_dist_m=0.0,
        min_remaining_dist_m=0.0,
        progress_percent=0,
    )
    s = update_session_from_current(s, Pose2D(x=1.0, y=2.0, map_id=0))
    assert 0 <= s.progress_percent <= 99


def test_progress_small_distance_starts_low():
    """Test that when total distance is very small but robot is away from goal, progress starts low."""
    start = Pose2D(x=1.0, y=2.0, map_id=0)
    goal = Pose2D(x=1.01, y=2.0, map_id=0)  # Very close start and goal (0.01m)
    total = dist_m(start, goal)
    assert total < 0.05  # Less than eps_m
    
    s = NavigationSession(
        command_id="c3",
        target_id="P3",
        start=start,
        goal=goal,
        total_dist_m=total,
        min_remaining_dist_m=total,
        progress_percent=0,
    )
    
    # Robot is at start position (away from goal by total distance)
    s = update_session_from_current(s, Pose2D(x=1.0, y=2.0, map_id=0))
    # Progress should start low (1-5%), not jump to 99%
    assert s.progress_percent < 10, f"Progress should start low, got {s.progress_percent}%"
    
    # Robot moves closer to goal
    s = update_session_from_current(s, Pose2D(x=1.005, y=2.0, map_id=0))
    # Progress should increase
    assert s.progress_percent >= 1
    
    # Robot reaches goal
    s = update_session_from_current(s, Pose2D(x=1.01, y=2.0, map_id=0))
    # Progress should be high but not 100% (clamped to 99%)
    assert s.progress_percent >= 90
    assert s.progress_percent <= 99

