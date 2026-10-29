"""集成测试：执行器 + 真实 Mock 工具 + 本地文件存储。"""

import json
import time
from dataclasses import replace
from pathlib import Path

from app.bootstrap import build_executor
from app.parser import parse_instruction
from app.tools.base import ToolBusy
from app.tools.robot_execute import RobotMock
from tests.constants import SUCCESS_INSTRUCTION


def test_every_step_persisted(settings):
    plan = parse_instruction(SUCCESS_INSTRUCTION, settings)
    run = build_executor(settings).run(plan, SUCCESS_INSTRUCTION)

    path = Path(settings.data_dir) / f"{run['run_id']}.json"
    assert path.exists()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["status"] == "success"
    assert len(saved["steps"]) == len(run["steps"]) == 9
    assert saved["record"]["final_yield_rate"] == 0.65
    assert saved["plan"]["branch"]["max_rounds"] == 2


class FlakyOnceRobot(RobotMock):
    """首次调用 transient 失败，之后恢复。"""

    def __init__(self):
        super().__init__(fault_rate=0.0, seed=1)
        self.failed_once = False

    def execute(self, action, params, *, idempotency_key):
        if not self.failed_once:
            self.failed_once = True
            raise ToolBusy("控制器忙")
        return super().execute(action, params, idempotency_key=idempotency_key)


def test_transient_fault_recovered(settings):
    plan = parse_instruction(SUCCESS_INSTRUCTION, settings)
    run = build_executor(settings, robot=FlakyOnceRobot()).run(plan, SUCCESS_INSTRUCTION)

    assert run["status"] == "success"
    assert run["steps"][0]["attempts"] == 2


class LostAckRobot:
    """第一次调用：动作已执行但回包超时；重试必须命中设备侧幂等台账。"""

    def __init__(self):
        self.physical = 0
        self.applied: set[str] = set()

    def execute(self, action, params, *, idempotency_key):
        if idempotency_key in self.applied:
            return {"action": action, "applied": True, "duplicate": True}
        self.applied.add(idempotency_key)
        self.physical += 1
        if self.physical == 1:
            time.sleep(0.3)  # 超过 tool_timeout_s
        return {"action": action, "applied": True}


def test_timeout_then_duplicate_guard(settings):
    robot = LostAckRobot()
    tight = replace(settings, tool_timeout_s=0.1)
    plan = parse_instruction(SUCCESS_INSTRUCTION, tight)
    run = build_executor(tight, robot=robot).run(plan, SUCCESS_INSTRUCTION)

    assert run["status"] == "success"
    robot_steps = [s for s in run["steps"] if s["tool"] == "robot_execute"]
    assert robot.physical == len(robot_steps) == 7  # 7 次机器人动作，无重复物理执行
    assert run["steps"][0]["attempts"] == 2
    assert run["steps"][0]["duplicate_guard"] is True
    assert run["record"]["duplicate_guarded"] == ["s1"]


def test_run_artifacts_reloadable(settings):
    plan = parse_instruction(SUCCESS_INSTRUCTION, settings)
    executor = build_executor(settings)
    run = executor.run(plan, SUCCESS_INSTRUCTION)

    loaded = executor.store.load(run["run_id"])
    assert loaded is not None
    assert loaded["run_id"] == run["run_id"]
    assert loaded["record"]["status"] == "success"
    assert executor.store.load("not-a-run") is None
