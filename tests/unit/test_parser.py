"""单元测试：自然语言解析。"""

import pytest

from app.errors import InstructionParseError, PlanInvalidError
from app.parser import parse_instruction
from tests.constants import SUCCESS_INSTRUCTION


def test_parse_full_instruction(settings):
    plan = parse_instruction(SUCCESS_INSTRUCTION, settings)

    assert plan.substrate == "ethyl_acetate"
    assert plan.branch.threshold == 0.6
    assert plan.branch.max_rounds == 2
    assert [s.id for s in plan.setup_steps] == ["s1", "s2", "s3", "s4", "s5"]
    assert plan.setup_steps[0].params == {
        "reagent": "ethyl_acetate",
        "concentration_mol_l": 0.1,
        "volume_ml": 2.0,
    }
    assert plan.setup_steps[1].params["target_temp_c"] == 45.0
    assert plan.setup_steps[2].params["duration_min"] == 30.0
    assert plan.repeat_steps[0].params["target_temp_c"] == 50.0
    assert plan.setup_steps[-1].tool == "chem_prediction"
    assert plan.warnings == []


def test_parse_missing_temperature(settings):
    text = "配置 0.1mol/L 的乙酸乙酯溶液，取 2ml，搅拌 30 分钟"
    with pytest.raises(InstructionParseError) as exc:
        parse_instruction(text, settings)
    assert exc.value.code == "invalid_instruction"
    assert "温度" in exc.value.message
    assert "hint" in exc.value.detail


def test_parse_too_short(settings):
    with pytest.raises(InstructionParseError):
        parse_instruction("你好", settings)


def test_threshold_defaults_with_warning(settings):
    text = "配置 0.1mol/L 的乙酸乙酯溶液，取 2ml，升温到 45℃，搅拌 30 分钟，取样检测，重复一轮"
    plan = parse_instruction(text, settings)
    assert plan.branch.threshold == 0.6
    assert any("默认" in w for w in plan.warnings)


def test_rounds_capped_by_settings(settings):
    plan = parse_instruction(SUCCESS_INSTRUCTION.replace("重复一轮", "重复五轮"), settings)
    assert plan.branch.max_rounds == settings.max_rounds
    assert any("上限" in w for w in plan.warnings)


def test_unit_variants(settings):
    text = (
        "配置 0.2mol/L 的乙醇溶液，取 3mL，升温到 55°C，搅拌 20min，取样检测，"
        "产率低于 70% 时升温到 60℃ 重复一轮"
    )
    plan = parse_instruction(text, settings)
    assert plan.setup_steps[0].params["volume_ml"] == 3.0
    assert plan.setup_steps[0].params["concentration_mol_l"] == 0.2
    assert plan.setup_steps[1].params["target_temp_c"] == 55.0
    assert plan.setup_steps[2].params["duration_min"] == 20.0
    assert plan.repeat_steps[0].params["target_temp_c"] == 60.0
    assert plan.branch.threshold == 0.7


def test_invalid_threshold_rejected(settings):
    with pytest.raises(PlanInvalidError):
        parse_instruction(SUCCESS_INSTRUCTION.replace("低于 60%", "低于 150%"), settings)
