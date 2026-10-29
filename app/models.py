"""计划与步骤的数据结构（纯 dataclass，便于直接转 JSON 落盘）。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Step:
    id: str
    name: str
    tool: str  # robot_execute | chem_prediction
    params: dict[str, Any]
    action: str | None = None  # 仅 robot_execute 使用

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "tool": self.tool,
            "action": self.action,
            "params": self.params,
        }


@dataclass
class Branch:
    """产率阈值分支：未达标则执行 repeat_steps，直到达到轮次上限。"""

    metric: str
    op: str  # >= > <= <
    threshold: float
    max_rounds: int  # 含首轮

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "op": self.op,
            "threshold": self.threshold,
            "max_rounds": self.max_rounds,
        }


@dataclass
class ExperimentPlan:
    title: str
    substrate: str
    setup_steps: list[Step]
    repeat_steps: list[Step]
    branch: Branch
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "substrate": self.substrate,
            "setup_steps": [s.to_dict() for s in self.setup_steps],
            "repeat_steps": [s.to_dict() for s in self.repeat_steps],
            "branch": self.branch.to_dict(),
            "warnings": self.warnings,
        }
