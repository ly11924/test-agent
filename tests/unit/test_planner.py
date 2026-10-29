"""单元测试：计划与参数校验。"""

import pytest

from app.errors import PlanInvalidError
from app.parser import parse_instruction
from app.planner import validate_action, validate_plan
from tests.constants import BAD_VOLUME_INSTRUCTION, SUCCESS_INSTRUCTION


def test_volume_out_of_range_rejected(settings):
    with pytest.raises(PlanInvalidError) as exc:
        parse_instruction(BAD_VOLUME_INSTRUCTION, settings)
    assert exc.value.code == "invalid_param"
    assert "volume_ml" in exc.value.message


def test_temperature_out_of_range_rejected(settings):
    with pytest.raises(PlanInvalidError):
        parse_instruction(SUCCESS_INSTRUCTION.replace("升温到 45℃", "升温到 300℃"), settings)


def test_unknown_action_rejected():
    with pytest.raises(PlanInvalidError) as exc:
        validate_action("laser_cut", {})
    assert "不支持的机器人动作" in exc.value.message


def test_unknown_param_rejected():
    with pytest.raises(PlanInvalidError) as exc:
        validate_action("stir", {"duration_min": 30, "speed_rpm": 100})
    assert "未知参数" in exc.value.message


def test_missing_param_rejected():
    with pytest.raises(PlanInvalidError) as exc:
        validate_action("heat", {})
    assert "缺少参数" in exc.value.message


def test_bool_is_not_a_number():
    with pytest.raises(PlanInvalidError):
        validate_action("stir", {"duration_min": True})


def test_setup_must_end_with_prediction(settings):
    plan = parse_instruction(SUCCESS_INSTRUCTION, settings)
    plan.setup_steps = plan.setup_steps[:-1]
    with pytest.raises(PlanInvalidError) as exc:
        validate_plan(plan, max_rounds_cap=settings.max_rounds)
    assert "产率检测" in exc.value.message


def test_duplicate_step_ids_rejected(settings):
    plan = parse_instruction(SUCCESS_INSTRUCTION, settings)
    plan.repeat_steps[0].id = "s1"
    with pytest.raises(PlanInvalidError):
        validate_plan(plan, max_rounds_cap=settings.max_rounds)


def test_threshold_range(settings):
    plan = parse_instruction(SUCCESS_INSTRUCTION, settings)
    plan.branch.threshold = 1.5
    with pytest.raises(PlanInvalidError):
        validate_plan(plan, max_rounds_cap=settings.max_rounds)


def test_rounds_clamped_to_cap(settings):
    plan = parse_instruction(SUCCESS_INSTRUCTION, settings)
    plan.branch.max_rounds = 99
    validate_plan(plan, max_rounds_cap=2)
    assert plan.branch.max_rounds == 2
