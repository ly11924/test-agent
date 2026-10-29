"""运行参数：全部可用环境变量覆盖，Demo 不引入配置文件。"""

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    tool_timeout_s: float = 1.0  # 单次工具调用超时
    max_tool_retries: int = 2  # 仅 transient 错误重试
    max_rounds: int = 3  # 总轮次硬上限（含首轮）
    total_timeout_s: float = 60.0  # 单次实验总时限
    max_steps: int = 20  # 单次实验最大步骤数
    robot_fault_rate: float = 0.3  # Mock 机器人故障率
    robot_seed: int = 2  # 固定种子 -> 演示可复现（该种子下会触发超时重试）
    data_dir: Path = ROOT / "data" / "runs"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            tool_timeout_s=_env_float("TOOL_TIMEOUT_S", cls.tool_timeout_s),
            max_tool_retries=_env_int("TOOL_MAX_RETRIES", cls.max_tool_retries),
            max_rounds=_env_int("MAX_ROUNDS", cls.max_rounds),
            total_timeout_s=_env_float("TOTAL_TIMEOUT_S", cls.total_timeout_s),
            max_steps=_env_int("MAX_STEPS", cls.max_steps),
            robot_fault_rate=_env_float("ROBOT_FAULT_RATE", cls.robot_fault_rate),
            robot_seed=_env_int("ROBOT_SEED", cls.robot_seed),
            data_dir=Path(os.getenv("CHEM_DATA_DIR", str(cls.data_dir))),
        )
