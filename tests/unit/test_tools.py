"""单元测试：工具调用层（超时/重试/幂等）与两个 Mock 工具。"""

import time

import pytest

from app.tools.base import HardwareError, ParamRejected, ToolBusy, ToolCaller
from app.tools.chem_prediction import ChemModelMock
from app.tools.robot_execute import RobotMock


def test_robot_rejects_out_of_range_params():
    robot = RobotMock(fault_rate=0.0, seed=1)
    with pytest.raises(ParamRejected):
        robot.execute(
            "pipette",
            {"reagent": "ethyl_acetate", "concentration_mol_l": 0.1, "volume_ml": 500},
            idempotency_key="k1",
        )


def test_robot_idempotent_ledger():
    robot = RobotMock(fault_rate=0.0, seed=1)
    first = robot.execute("stir", {"duration_min": 30}, idempotency_key="k")
    second = robot.execute("stir", {"duration_min": 30}, idempotency_key="k")
    assert first["applied"] is True
    assert second["duplicate"] is True
    assert robot.physical_executions == 1


def test_caller_retries_transient_error():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ToolBusy("busy")
        return {"ok": True}

    result = ToolCaller(timeout_s=0.5, max_retries=2).call("robot_execute", flaky, "k")
    assert result.ok and result.attempts == 2 and calls["n"] == 2


def test_caller_stops_at_retry_cap():
    def always_busy():
        raise ToolBusy("busy")

    result = ToolCaller(timeout_s=0.5, max_retries=2).call("robot_execute", always_busy, "k")
    assert not result.ok
    assert result.attempts == 3
    assert result.error_code == "device_busy"


def test_caller_does_not_retry_hardware_error():
    def boom():
        raise HardwareError("机械臂回零失败 (E1043)")

    result = ToolCaller(timeout_s=0.5, max_retries=2).call("robot_execute", boom, "k")
    assert not result.ok
    assert result.attempts == 1
    assert result.error_code == "hardware_error"


def test_caller_timeout_is_transient():
    def slow():
        time.sleep(0.2)
        return {}

    result = ToolCaller(timeout_s=0.05, max_retries=1).call("robot_execute", slow, "k")
    assert not result.ok
    assert result.attempts == 2
    assert result.error_code == "timeout"


def test_caller_cache_prevents_reexecution():
    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return {"ok": True}

    caller = ToolCaller(timeout_s=0.5, max_retries=1)
    assert caller.call("robot_execute", counted, "same-key").ok
    cached = caller.call("robot_execute", counted, "same-key")
    assert cached.ok and cached.duplicate and calls["n"] == 1


def test_chem_model_monotonic_and_deterministic():
    model = ChemModelMock()
    at_45 = model.predict("ethyl_acetate", 45, 0.1)["yield_rate"]
    at_50 = model.predict("ethyl_acetate", 50, 0.1)["yield_rate"]
    assert at_50 > at_45
    assert model.predict("ethyl_acetate", 50, 0.1)["yield_rate"] == at_50
    assert 0.01 <= at_45 <= 0.99
