"""单元测试：执行器的分支、轮次上限、失败与预算控制。"""

from dataclasses import replace

from app.bootstrap import build_executor
from app.executor import ExperimentExecutor
from app.parser import parse_instruction
from app.store import RunStore
from app.tools.base import HardwareError
from app.tools.chem_prediction import ChemModelMock
from tests.constants import STRICT_INSTRUCTION, SUCCESS_INSTRUCTION


class BrokenRobot:
    """所有动作都返回硬件错误。"""

    def execute(self, action, params, *, idempotency_key):
        raise HardwareError("机械臂回零失败 (E1043)")


def test_success_in_first_round(executor, settings):
    plan = parse_instruction(SUCCESS_INSTRUCTION.replace("产率低于 60%", "产率低于 50%"), settings)
    run = executor.run(plan, SUCCESS_INSTRUCTION)

    assert run["status"] == "success"
    assert run["round"] == 1
    assert len(run["steps"]) == 5
    assert run["record"]["yield_history"] == [0.55]
    assert run["record"]["steps_failed"] == 0


def test_retry_round_makes_it(executor, settings):
    plan = parse_instruction(SUCCESS_INSTRUCTION, settings)
    run = executor.run(plan, SUCCESS_INSTRUCTION)

    assert run["status"] == "success"
    assert run["round"] == 2
    assert len(run["steps"]) == 9
    assert run["record"]["yield_history"] == [0.55, 0.65]
    assert "实验成功" in run["record"]["conclusion"]


def test_max_rounds_reached(executor, settings):
    plan = parse_instruction(STRICT_INSTRUCTION, settings)
    run = executor.run(plan, STRICT_INSTRUCTION)

    assert run["status"] == "max_rounds_reached"
    assert run["round"] == 2
    assert len(run["steps"]) == 9
    assert "轮次上限" in run["record"]["conclusion"]


def test_hardware_error_fails_run(settings):
    plan = parse_instruction(SUCCESS_INSTRUCTION, settings)
    executor = ExperimentExecutor(
        robot=BrokenRobot(),
        chem=ChemModelMock(),
        store=RunStore(settings.data_dir),
        settings=settings,
    )
    run = executor.run(plan, SUCCESS_INSTRUCTION)

    assert run["status"] == "failed"
    assert run["error"]["code"] == "hardware_error"
    assert run["steps"][0]["status"] == "failed"
    assert run["record"]["steps_failed"] == 1


def test_step_budget_enforced(settings):
    tight = replace(settings, max_steps=3)
    plan = parse_instruction(SUCCESS_INSTRUCTION, tight)
    run = build_executor(tight).run(plan, SUCCESS_INSTRUCTION)

    assert run["status"] == "failed"
    assert run["error"]["code"] == "max_steps_exceeded"
    assert len(run["steps"]) == 3


def test_max_rounds_cap_from_settings(settings):
    one_round = replace(settings, max_rounds=1)
    plan = parse_instruction(SUCCESS_INSTRUCTION, one_round)

    assert plan.branch.max_rounds == 1
    run = build_executor(one_round).run(plan, SUCCESS_INSTRUCTION)
    assert run["status"] == "max_rounds_reached"
    assert len(run["steps"]) == 5
