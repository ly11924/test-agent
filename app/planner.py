"""计划校验：进入执行器的动作与参数必须在白名单和物理范围内。"""

from app.errors import PlanInvalidError
from app.models import Branch, ExperimentPlan, Step

# 机器人动作白名单：参数名 -> 校验函数
_ROBOT_RULES = {
    "pipette": {
        "reagent": "str",
        "concentration_mol_l": (0.0, 5.0, True),
        "volume_ml": (0.0, 100.0, True),
    },
    "heat": {"target_temp_c": (0.0, 150.0, False)},
    "stir": {"duration_min": (0.0, 720.0, True)},
    "sample": {"container": "str"},
}

_OPS = (">=", ">", "<=", "<")


def _check_number(name: str, value, low: float, high: float, exclusive_low: bool) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanInvalidError(f"参数 {name} 必须是数字，收到 {value!r}")
    if value < low or value > high or (exclusive_low and value <= low):
        bound = f"({low}, {high}]" if exclusive_low else f"[{low}, {high}]"
        raise PlanInvalidError(
            f"参数 {name}={value} 超出允许范围 {bound}",
            {"param": name, "value": value, "allowed": bound},
        )


def _check_str(name: str, value) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PlanInvalidError(f"参数 {name} 必须是非空字符串")


def validate_action(action: str | None, params: dict) -> None:
    """动作白名单 + 参数名白名单 + 数值范围，三层校验。"""
    if action not in _ROBOT_RULES:
        raise PlanInvalidError(f"不支持的机器人动作: {action!r}")
    rules = _ROBOT_RULES[action]

    unknown = set(params) - set(rules)
    if unknown:
        raise PlanInvalidError(f"动作 {action} 含未知参数: {sorted(unknown)}")
    missing = set(rules) - set(params)
    if missing:
        raise PlanInvalidError(f"动作 {action} 缺少参数: {sorted(missing)}")

    for name, rule in rules.items():
        if rule == "str":
            _check_str(name, params[name])
        else:
            _check_number(name, params[name], *rule)


def validate_step(step: Step) -> None:
    if step.tool == "robot_execute":
        validate_action(step.action, step.params)
    elif step.tool == "chem_prediction":
        _check_str("substrate", step.params.get("substrate"))
        _check_number("temperature_c", step.params.get("temperature_c"), 0.0, 200.0, False)
        _check_number("concentration_mol_l", step.params.get("concentration_mol_l"), 0.0, 5.0, True)
    else:
        raise PlanInvalidError(f"未知工具: {step.tool!r}")


def validate_plan(plan: ExperimentPlan, *, max_rounds_cap: int) -> ExperimentPlan:
    """结构 + 分支 + 轮次上限校验，超限的轮次直接压到硬上限。"""
    if not plan.setup_steps:
        raise PlanInvalidError("计划缺少首轮步骤")
    if plan.setup_steps[-1].tool != "chem_prediction":
        raise PlanInvalidError("首轮最后一步必须是产率检测(chem_prediction)")
    if plan.repeat_steps and plan.repeat_steps[-1].tool != "chem_prediction":
        raise PlanInvalidError("重复轮最后一步必须是产率检测(chem_prediction)")

    ids = [s.id for s in plan.setup_steps + plan.repeat_steps]
    if len(ids) != len(set(ids)):
        raise PlanInvalidError(f"步骤 id 重复: {ids}")
    for step in plan.setup_steps + plan.repeat_steps:
        validate_step(step)

    branch = plan.branch
    if not isinstance(branch, Branch):
        raise PlanInvalidError("缺少分支定义")
    if branch.metric != "yield_rate":
        raise PlanInvalidError(f"不支持的判定指标: {branch.metric}")
    if branch.op not in _OPS:
        raise PlanInvalidError(f"不支持的比较符: {branch.op}")
    if not 0 < branch.threshold <= 1:
        raise PlanInvalidError(f"产率阈值必须在 (0, 1] 内，收到 {branch.threshold}")
    if branch.max_rounds < 1:
        raise PlanInvalidError("轮次上限必须 >= 1")

    if branch.max_rounds > max_rounds_cap:
        plan.warnings.append(f"轮次 {branch.max_rounds} 超过硬上限，已压到 {max_rounds_cap}")
        branch.max_rounds = max_rounds_cap
    return plan
