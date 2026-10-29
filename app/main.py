"""HTTP 接口：解析 -> 执行 -> 落盘，同步返回完整运行结果。"""

import re
from dataclasses import replace

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.bootstrap import build_executor
from app.config import Settings
from app.errors import AgentError
from app.parser import parse_instruction
from app.store import RunStore

app = FastAPI(title="Chem Experiment Agent (Demo)", version="0.1.0")

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RunOptions(BaseModel):
    robot_fault_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    robot_seed: int | None = None
    max_rounds: int | None = Field(default=None, ge=1, le=10)


class ExperimentRequest(BaseModel):
    instruction: str = Field(..., min_length=5, description="自然语言实验指令")
    options: RunOptions | None = None


@app.exception_handler(AgentError)
async def _agent_error_handler(request: Request, exc: AgentError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"code": "invalid_request", "message": "请求体格式错误", "detail": exc.errors()},
    )


@app.get("/health")
def health() -> dict:
    return {"code": 0, "status": "ok"}


@app.post("/api/experiments")
def create_experiment(req: ExperimentRequest) -> dict:
    settings = Settings.from_env()
    if req.options:
        overrides = {}
        if req.options.robot_fault_rate is not None:
            overrides["robot_fault_rate"] = req.options.robot_fault_rate
        if req.options.robot_seed is not None:
            overrides["robot_seed"] = req.options.robot_seed
        if req.options.max_rounds is not None:
            overrides["max_rounds"] = req.options.max_rounds
        settings = replace(settings, **overrides)

    plan = parse_instruction(req.instruction, settings)
    run = build_executor(settings).run(plan, req.instruction)
    return {"code": 0, "data": run}


@app.get("/api/experiments/{run_id}")
def get_experiment(run_id: str) -> dict:
    if not _RUN_ID_RE.match(run_id):
        raise HTTPException(status_code=400, detail="非法 run_id")
    run = RunStore(Settings.from_env().data_dir).load(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="实验记录不存在")
    return {"code": 0, "data": run}
