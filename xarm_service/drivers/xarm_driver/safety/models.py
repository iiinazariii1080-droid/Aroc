"""Safety check results and violations."""
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class Violation:
    """Single workspace violation."""
    point_name: str  # e.g. "tcp", "link_2"
    x: float
    y: float
    z: float
    axis: Optional[str] = None  # "x_min", "x_max", etc.
    margin_mm: float = 0.0


@dataclass
class CheckResult:
    """Result of workspace envelope check."""
    ok: bool
    violations: List[Violation] = field(default_factory=list)

    @classmethod
    def success(cls) -> "CheckResult":
        return cls(ok=True, violations=[])

    @classmethod
    def fail(cls, violations: List[Violation]) -> "CheckResult":
        return cls(ok=False, violations=violations)
