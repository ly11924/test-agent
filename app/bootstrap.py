"""组装依赖，供 API / CLI / 测试复用。"""

from app.config import Settings
from app.executor import ExperimentExecutor
from app.store import RunStore
from app.tools.chem_prediction import ChemModelMock
from app.tools.robot_execute import RobotMock


def build_executor(settings: Settings, robot: RobotMock | None = None) -> ExperimentExecutor:
    robot = robot or RobotMock(
        fault_rate=settings.robot_fault_rate,
        seed=settings.robot_seed,
        hang_s=settings.tool_timeout_s + 0.5,
    )
    return ExperimentExecutor(
        robot=robot,
        chem=ChemModelMock(),
        store=RunStore(settings.data_dir),
        settings=settings,
    )
