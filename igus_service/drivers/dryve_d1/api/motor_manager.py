from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .drive import DryveD1, DryveD1Config


@dataclass
class MotorManager:
    """Optional multi-axis orchestration.

    This is intentionally lightweight: it helps you create multiple DryveD1 instances,
    connect/close them as a group, and access them by name.

    For true multi-axis motion coordination (synchronized trajectories),
    build a higher-level planner on top.
    """

    axes: dict[str, DryveD1] = field(default_factory=dict)

    @classmethod
    def from_configs(cls, configs: dict[str, DryveD1Config]) -> MotorManager:
        axes = {name: DryveD1(config=cfg) for name, cfg in configs.items()}
        return cls(axes=axes)

    def add_axis(self, name: str, axis: DryveD1) -> None:
        if name in self.axes:
            raise ValueError(f"Axis '{name}' already exists")
        self.axes[name] = axis

    def get(self, name: str) -> DryveD1:
        try:
            return self.axes[name]
        except KeyError as e:
            raise KeyError(f"Unknown axis '{name}'") from e

    async def connect_all(self) -> None:
        await asyncio.gather(*(axis.connect() for axis in self.axes.values()))

    async def close_all(self) -> None:
        await asyncio.gather(*(axis.close() for axis in self.axes.values()))

    async def enable_all(self) -> None:
        await asyncio.gather(*(axis.enable_operation() for axis in self.axes.values()))
