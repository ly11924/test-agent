"""命令行入口：python -m app.cli "<自然语言指令>" """

import argparse
import json
from dataclasses import replace

from app.bootstrap import build_executor
from app.config import Settings
from app.errors import AgentError
from app.parser import parse_instruction


def print_summary(run: dict) -> None:
    rec = run["record"]
    plan = run["plan"]
    print(f"run_id : {run['run_id']}")
    print(f"status : {run['status']}")
    print(
        f"plan   : 首轮 {len(plan['setup_steps'])} 步 / 重复 {len(plan['repeat_steps'])} 步, "
        f"阈值 {plan['branch']['threshold']:.0%}, 轮次上限 {plan['branch']['max_rounds']}"
    )
    for warning in rec["warnings"]:
        print(f"warn   : {warning}")
    print("\n步骤执行:")
    for step in run["steps"]:
        line = (
            f"  R{step['round']} {step['step_id']:<3} {step['name']:<6} "
            f"{step['tool']:<15} {step['status']:<7} attempts={step['attempts']} "
            f"{step['duration_ms']:>5}ms"
        )
        if step["duplicate_guard"]:
            line += " [幂等命中]"
        if step["error"]:
            line += f" error={step['error']['code']}"
        print(line)
    print(f"\n产率轨迹: {rec['yield_history']}")
    print(f"结论: {rec['conclusion']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="化学实验 Agent Demo (方案A: 确定性编排)")
    parser.add_argument("instruction", help="自然语言实验指令")
    parser.add_argument("--fault-rate", type=float, default=None, help="Mock 机器人故障率 0~1")
    parser.add_argument("--seed", type=int, default=None, help="故障随机种子（固定后演示可复现）")
    parser.add_argument("--max-rounds", type=int, default=None, help="总轮次硬上限")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    overrides = {}
    if args.fault_rate is not None:
        overrides["robot_fault_rate"] = args.fault_rate
    if args.seed is not None:
        overrides["robot_seed"] = args.seed
    if args.max_rounds is not None:
        overrides["max_rounds"] = args.max_rounds
    settings = replace(settings, **overrides)

    try:
        plan = parse_instruction(args.instruction, settings)
        run = build_executor(settings).run(plan, args.instruction)
    except AgentError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2))
        return 2

    if args.json:
        print(
            json.dumps(
                {key: run[key] for key in ("run_id", "status", "plan", "steps", "result", "error", "record")},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print_summary(run)
    return 0 if run["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
