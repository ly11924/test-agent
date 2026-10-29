"""确定性执行器：按计划顺序执行步骤，代码判断产率分支，不是 LLM 决策。"""

import time
from datetime import datetime
from uuid import uuid4

from app.config import Settings
from app.models import ExperimentPlan, Step
from app.store import RunStore
from app.tools.base import ToolCaller

_OPS = {
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ExperimentExecutor:
    def __init__(self, *, robot, chem, store: RunStore, settings: Settings):
        self.robot = robot
        self.chem = chem
        self.store = store
        self.settings = settings

    def run(self, plan: ExperimentPlan, instruction: str) -> dict:
        run = {
            "run_id": f"exp-{datetime.now():%Y%m%d%H%M%S}-{uuid4().hex[:6]}",
            "instruction": instruction,
            "plan": plan.to_dict(),
            "status": "running",
            "round": 1,
            "started_at": _now(),
            "finished_at": None,
            "steps": [],
            "result": {},
            "error": None,
            "record": {},
        }
        self.store.save(run)
        caller = ToolCaller(
            timeout_s=self.settings.tool_timeout_s,
            max_retries=self.settings.max_tool_retries,
        )
        try:
            self._drive(run, plan, caller)
        except Exception as exc:  # 兜底：任何异常都转成失败记录，不抛 500
            run["status"] = "failed"
            run["error"] = {"code": "internal_error", "message": str(exc)}
        run["finished_at"] = _now()
        run["record"] = self._build_record(run, plan)
        self.store.save(run)
        return run

    def _drive(self, run: dict, plan: ExperimentPlan, caller: ToolCaller) -> None:
        deadline = time.monotonic() + self.settings.total_timeout_s
        branch = plan.branch

        yields = [self._run_phase(run, plan.setup_steps, 1, caller, deadline)]
        if run["status"] != "running":
            return

        # 条件回跳：代码比较产率，轮次受硬上限约束
        while not _OPS[branch.op](yields[-1], branch.threshold) and run["round"] < branch.max_rounds:
            run["round"] += 1
            yields.append(self._run_phase(run, plan.repeat_steps, run["round"], caller, deadline))
            if run["status"] != "running":
                return

        passed = _OPS[branch.op](yields[-1], branch.threshold)
        run["status"] = "success" if passed else "max_rounds_reached"
        run["result"] = {
            "final_yield_rate": yields[-1],
            "yield_history": yields,
            "rounds_executed": run["round"],
            "threshold": branch.threshold,
            "passed": passed,
        }

    def _run_phase(
        self, run: dict, steps: list[Step], round_no: int, caller: ToolCaller, deadline: float
    ) -> float:
        yield_rate = 0.0
        for step in steps:
            if len(run["steps"]) >= self.settings.max_steps:
                self._abort(run, "max_steps_exceeded", "超过最大步骤数，已终止")
                return 0.0
            if time.monotonic() > deadline:
                self._abort(run, "total_timeout", "超过实验总执行时限，已终止")
                return 0.0

            entry, result = self._execute_step(run, step, round_no, caller)
            run["steps"].append(entry)
            self.store.save(run)  # 每步落盘

            if not result.ok:
                self._abort(
                    run,
                    result.error_code or "tool_error",
                    f"步骤 {step.id}({step.name}) 失败: {result.error_message}",
                    step=step,
                )
                return 0.0
            if step.tool == "chem_prediction":
                yield_rate = float(result.value["yield_rate"])
        return yield_rate

    def _execute_step(self, run: dict, step: Step, round_no: int, caller: ToolCaller):
        key = f"{run['run_id']}:{round_no}:{step.id}"
        started = time.perf_counter()

        if step.tool == "robot_execute":
            result = caller.call(
                "robot_execute",
                lambda: self.robot.execute(step.action, step.params, idempotency_key=key),
                key,
            )
        else:
            params = step.params
            result = caller.call(
                "chem_prediction",
                lambda: self.chem.predict(
                    params["substrate"], params["temperature_c"], params["concentration_mol_l"]
                ),
                key,
            )

        entry = {
            "round": round_no,
            "step_id": step.id,
            "name": step.name,
            "tool": step.tool,
            "action": step.action,
            "params": step.params,
            "status": "success" if result.ok else "failed",
            "attempts": result.attempts,
            # 命中调用层缓存或设备侧台账都算幂等保护
            "duplicate_guard": result.duplicate or bool((result.value or {}).get("duplicate")),
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "output": result.value,
            "error": None
            if result.ok
            else {"code": result.error_code, "message": result.error_message},
        }
        return entry, result

    def _abort(self, run: dict, code: str, message: str, *, step: Step | None = None) -> None:
        run["status"] = "failed"
        run["error"] = {
            "code": code,
            "message": message,
            "step_id": step.id if step else None,
        }

    def _build_record(self, run: dict, plan: ExperimentPlan) -> dict:
        steps = run["steps"]
        final_yield = run["result"].get("final_yield_rate")
        if run["status"] == "success":
            conclusion = (
                f"实验成功：第 {run['round']} 轮产率 {final_yield:.1%} ≥ 阈值 "
                f"{plan.branch.threshold:.0%}"
            )
        elif run["status"] == "max_rounds_reached":
            conclusion = (
                f"达到轮次上限 {plan.branch.max_rounds}：最终产率 {final_yield:.1%} < 阈值 "
                f"{plan.branch.threshold:.0%}，需人工介入"
            )
        else:
            conclusion = f"实验失败：{run['error']['message'] if run['error'] else '未知原因'}"
        return {
            "run_id": run["run_id"],
            "title": plan.title,
            "substrate": plan.substrate,
            "status": run["status"],
            "conclusion": conclusion,
            "threshold": plan.branch.threshold,
            "rounds_executed": run["round"],
            "final_yield_rate": final_yield,
            "yield_history": run["result"].get("yield_history", []),
            "warnings": plan.warnings,
            "steps_total": len(steps),
            "steps_failed": sum(1 for s in steps if s["status"] == "failed"),
            "retried_steps": [s["step_id"] for s in steps if s["attempts"] > 1],
            "duplicate_guarded": [s["step_id"] for s in steps if s["duplicate_guard"]],
            "error": run["error"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
        }
