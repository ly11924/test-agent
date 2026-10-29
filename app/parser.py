"""自然语言 -> 结构化实验计划。

Demo 使用规则解析（零外部依赖、结果确定）；对外契约固定为 ExperimentPlan，
后续可替换为 LLM 结构化输出（JSON Schema 校验后再走同一个 validate_plan）。
"""

import re

from app.config import Settings
from app.errors import InstructionParseError
from app.models import Branch, ExperimentPlan, Step
from app.planner import validate_plan

_HINT = (
    "示例：配置 0.1mol/L 的乙酸乙酯溶液，取 2ml，升温到 45℃，搅拌 30 分钟，"
    "取样检测，产率低于 60% 时升温到 50℃ 重复一轮。"
)

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5}
SUBSTRATE_ALIASES = {"乙酸乙酯": "ethyl_acetate", "乙醇": "ethanol", "丙酮": "acetone"}

_CONCENTRATION_RE = re.compile(r"([\d.]+)\s*mol\s*/\s*[Ll]")
_VOLUME_RE = re.compile(r"取\s*([\d.]+)\s*(?:ml|mL|毫升)")
_TEMP_RE = re.compile(r"(?:升温|加热|降温|温度|调温)\s*(?:到|至)?\s*([\d.]+)\s*(?:℃|°C|度)")
_STIR_RE = re.compile(r"搅拌\s*([\d.]+)\s*(?:分钟|min)")
_FAIL_RE = re.compile(r"产率\s*(?:低于|小于|≤|<)\s*([\d.]+)\s*%")
_PASS_RE = re.compile(r"产率\s*(?:≥|>=|大于|高于|不低于)\s*([\d.]+)\s*%")
_ROUNDS_RE = re.compile(r"重复\s*([一二两三四五]|\d+)\s*轮")
_SUBSTRATE_RE = re.compile(r"的\s*([\u4e00-\u9fa5A-Za-z0-9\-]{2,20})\s*溶液")


def _first_float(pattern: re.Pattern, text: str) -> float | None:
    match = pattern.search(text)
    return float(match.group(1)) if match else None


def _build_steps(
    substrate: str,
    concentration: float,
    volume: float,
    first_temp: float,
    repeat_temp: float,
    duration: float,
) -> tuple[list[Step], list[Step]]:
    setup = [
        Step(
            "s1",
            "移液配制",
            "robot_execute",
            {"reagent": substrate, "concentration_mol_l": concentration, "volume_ml": volume},
            action="pipette",
        ),
        Step("s2", "升温", "robot_execute", {"target_temp_c": first_temp}, action="heat"),
        Step("s3", "搅拌", "robot_execute", {"duration_min": duration}, action="stir"),
        Step("s4", "取样", "robot_execute", {"container": "reactor_1"}, action="sample"),
        Step(
            "s5",
            "产率检测",
            "chem_prediction",
            {
                "substrate": substrate,
                "temperature_c": first_temp,
                "concentration_mol_l": concentration,
            },
        ),
    ]
    repeat = [
        Step("r1", "复温调整", "robot_execute", {"target_temp_c": repeat_temp}, action="heat"),
        Step("r2", "搅拌", "robot_execute", {"duration_min": duration}, action="stir"),
        Step("r3", "取样", "robot_execute", {"container": "reactor_1"}, action="sample"),
        Step(
            "r4",
            "产率检测",
            "chem_prediction",
            {
                "substrate": substrate,
                "temperature_c": repeat_temp,
                "concentration_mol_l": concentration,
            },
        ),
    ]
    return setup, repeat


def parse_instruction(text: str, settings: Settings) -> ExperimentPlan:
    if not isinstance(text, str) or len(text.strip()) < 5:
        raise InstructionParseError("指令为空或过短", {"hint": _HINT})
    text = text.strip()

    concentration = _first_float(_CONCENTRATION_RE, text)
    volume = _first_float(_VOLUME_RE, text)
    temps = [float(t) for t in _TEMP_RE.findall(text)]
    duration = _first_float(_STIR_RE, text)
    threshold_pct = _first_float(_FAIL_RE, text) or _first_float(_PASS_RE, text)
    substrate_match = _SUBSTRATE_RE.search(text)

    missing = []
    if concentration is None:
        missing.append("浓度（如 0.1mol/L）")
    if volume is None:
        missing.append("体积（如 取 2ml）")
    if not temps:
        missing.append("温度（如 升温到 45℃）")
    if duration is None:
        missing.append("搅拌时长（如 搅拌 30 分钟）")
    if substrate_match is None:
        missing.append("反应物（如 乙酸乙酯溶液）")
    if missing:
        raise InstructionParseError("缺少关键参数：" + "、".join(missing), {"hint": _HINT})

    warnings: list[str] = []
    if threshold_pct is None:
        threshold_pct = 60.0
        warnings.append("未识别产率阈值，默认 60%")

    substrate_raw = substrate_match.group(1)
    substrate = SUBSTRATE_ALIASES.get(substrate_raw, substrate_raw)
    if substrate_raw not in SUBSTRATE_ALIASES:
        warnings.append(f"未知反应物 {substrate_raw}，按原文透传")

    first_temp = temps[0]
    if len(temps) > 1:
        repeat_temp = temps[1]
    else:
        repeat_temp = min(first_temp + 5.0, 150.0)
        warnings.append(f"未识别复温温度，默认 {repeat_temp:g}℃")

    repeat_rounds = 1
    rounds_match = _ROUNDS_RE.search(text)
    if rounds_match:
        token = rounds_match.group(1)
        repeat_rounds = int(token) if token.isdigit() else _CN_NUM.get(token, 1)

    setup, repeat = _build_steps(substrate, concentration, volume, first_temp, repeat_temp, duration)
    plan = ExperimentPlan(
        title=f"{substrate_raw} 产率实验",
        substrate=substrate,
        setup_steps=setup,
        repeat_steps=repeat,
        branch=Branch(
            metric="yield_rate",
            op=">=",
            threshold=round(threshold_pct / 100.0, 4),
            max_rounds=1 + repeat_rounds,
        ),
        warnings=warnings,
    )
    return validate_plan(plan, max_rounds_cap=settings.max_rounds)
