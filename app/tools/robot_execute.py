"""Mock 机器人底层接口：随机超时 / 硬件报错 / 拒绝非法参数。

- 幂等台账 applied：同一 idempotency_key 只做一次物理动作
- fault_rate=0 时行为完全确定，便于测试
"""

import random
import time

from app.errors import PlanInvalidError
from app.planner import validate_action
from app.tools.base import HardwareError, ParamRejected, ToolBusy


class RobotMock:
    def __init__(self, *, fault_rate: float = 0.3, seed: int = 2024, hang_s: float = 1.5):
        self.fault_rate = fault_rate
        self.rng = random.Random(seed)
        self.hang_s = hang_s
        self.applied: dict[str, dict] = {}
        self.physical_executions = 0

    def execute(self, action: str, params: dict, *, idempotency_key: str) -> dict:
        if idempotency_key in self.applied:
            return {**self.applied[idempotency_key], "duplicate": True}

        try:
            validate_action(action, params)  # 设备侧再校验一次
        except PlanInvalidError as exc:
            raise ParamRejected(exc.message) from exc

        fault = self._fault()
        if fault == "timeout":
            # 动作已下发、回包丢失：先登记台账，再模拟挂起
            self.physical_executions += 1
            self.applied[idempotency_key] = {"action": action, "applied": True}
            time.sleep(self.hang_s)
            return dict(self.applied[idempotency_key])
        if fault == "busy":
            raise ToolBusy("机器人控制器忙，指令未受理")
        if fault == "hardware":
            raise HardwareError("机械臂回零失败 (E1043)")
        if fault == "reject":
            raise ParamRejected("设备端拒绝：参数超出物理行程")

        self.physical_executions += 1
        result = {
            "action": action,
            "params": params,
            "applied": True,
            "device": "robot-sim-01",
        }
        self.applied[idempotency_key] = result
        return result

    def _fault(self) -> str | None:
        if self.fault_rate <= 0:
            return None
        roll = self.rng.random()
        rate = self.fault_rate
        if roll < rate * 0.55:
            return "timeout"
        if roll < rate * 0.80:
            return "busy"
        if roll < rate * 0.95:
            return "hardware"
        if roll < rate:
            return "reject"
        return None
