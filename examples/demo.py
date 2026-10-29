"""三个演示场景：成功闭环 / 到达轮次上限 / 参数非法被拒。

运行：python examples/demo.py
"""

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bootstrap import build_executor  # noqa: E402
from app.config import Settings  # noqa: E402
from app.errors import AgentError  # noqa: E402
from app.parser import parse_instruction  # noqa: E402

SUCCESS_INSTRUCTION = (
    "配置 0.1mol/L 的乙酸乙酯溶液，取 2ml，升温到 45℃，搅拌 30 分钟，"
    "然后取样检测，若产率低于 60%，则重新调整温度到 50℃再重复一轮；"
    "如果产率≥60%，实验结束，生成实验记录。"
)
STRICT_INSTRUCTION = SUCCESS_INSTRUCTION.replace("产率低于 60%", "产率低于 80%")
BAD_PARAM_INSTRUCTION = SUCCESS_INSTRUCTION.replace("取 2ml", "取 500ml")


def show(title: str, settings: Settings, instruction: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    print(f"指令: {instruction}")
    try:
        plan = parse_instruction(instruction, settings)
        run = build_executor(settings).run(plan, instruction)
    except AgentError as exc:
        payload = exc.to_dict()
        print(f"拦截: code={payload['code']} message={payload['message']}")
        return

    rec = run["record"]
    print(f"run_id: {run['run_id']}  status: {run['status']}")
    print(f"阈值: {plan.branch.threshold:.0%}  轮次上限: {plan.branch.max_rounds}  实际轮次: {run['round']}")
    print("-" * 78)
    for step in run["steps"]:
        extra = f"  attempts={step['attempts']}" if step["attempts"] else ""
        dup = "  [幂等命中]" if step["duplicate_guard"] else ""
        err = f"  error={step['error']['code']}" if step["error"] else ""
        print(
            f"R{step['round']}  {step['step_id']:<3} {step['name']:<6} "
            f"{step['tool']:<15} {step['status']:<7} {step['duration_ms']:>5}ms{extra}{dup}{err}"
        )
    print("-" * 78)
    print(f"产率轨迹: {rec['yield_history']}")
    if rec["retried_steps"]:
        print(f"发生重试: {rec['retried_steps']}   幂等保护: {rec['duplicate_guarded']}")
    print(f"结论: {rec['conclusion']}")
    print(f"记录文件: data/runs/{run['run_id']}.json")


def main() -> None:
    base = Settings.from_env()

    # 场景1：真实成功路径（30% 故障率，靠重试 + 幂等兜底走完）
    show(
        "场景1 成功闭环（Mock 机器人 30% 故障率）",
        replace(base, robot_fault_rate=0.3, robot_seed=base.robot_seed),
        SUCCESS_INSTRUCTION,
    )
    # 场景2：阈值 80%，两轮都不达标 -> 到达轮次上限
    show(
        "场景2 到达轮次上限（阈值 80%）",
        replace(base, robot_fault_rate=0.0),
        STRICT_INSTRUCTION,
    )
    # 场景3：体积 500ml 超出白名单范围 -> 调用前拦截
    show(
        "场景3 参数非法拒绝（取 500ml）",
        replace(base, robot_fault_rate=0.0),
        BAD_PARAM_INSTRUCTION,
    )


if __name__ == "__main__":
    main()
