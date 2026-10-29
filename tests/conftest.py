"""pytest 公共配置：保证从项目根导入 app，并统一使用临时数据目录。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app.bootstrap import build_executor  # noqa: E402
from app.config import Settings  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        tool_timeout_s=0.2,
        max_tool_retries=2,
        max_rounds=3,
        total_timeout_s=20.0,
        max_steps=20,
        robot_fault_rate=0.0,
        robot_seed=2,
        data_dir=tmp_path / "runs",
    )


@pytest.fixture
def executor(settings: Settings):
    return build_executor(settings)
