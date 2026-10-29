# 化学实验 Agent（Demo · 方案A：确定性编排）

**一句话架构**：LLM（Demo 中为规则解析器）只负责把自然语言解析成结构化实验计划；流程控制、条件分支、重试与记录全部由确定性执行器完成。
没有 ReAct 循环，没有第三方 Agent 框架，LLM 每条指令只调用一次。

```
自然语言指令
   │
   ▼
parser.py      规则/LLM 解析  →  ExperimentPlan（步骤列表 + 阈值 + 轮次上限）
   │
   ▼
planner.py     动作白名单 / 参数范围 / 轮次硬上限校验（非法参数在这里被拦截）
   │
   ▼
executor.py    确定性状态机：逐步执行 → 代码比较产率 → 决定是否回跳一轮
   │                    │
   │                    ├─ tools/base.py       超时 / 错误分类 / 重试上限 / 幂等缓存
   │                    ├─ tools/robot_execute 移液、加热、搅拌、取样（可注入故障）
   │                    └─ tools/chem_prediction Mock 化学模型（返回产率）
   ▼
store.py       每步落盘 data/runs/<run_id>.json → 生成实验记录
```

## 目录结构

```
test-agent/
├── app/
│   ├── main.py              # FastAPI 路由：POST /api/experiments、GET /api/experiments/{run_id}
│   ├── cli.py               # 命令行入口：python -m app.cli "<指令>"
│   ├── parser.py            # 自然语言 -> ExperimentPlan（规则解析，可替换为 LLM）
│   ├── planner.py           # 计划校验：动作白名单、参数范围、阈值、轮次上限
│   ├── executor.py          # 确定性执行器：分支判断、轮次控制、步骤预算、记录生成
│   ├── models.py            # Step / Branch / ExperimentPlan 数据结构
│   ├── store.py             # 本地文件存储（原子写入，无数据库）
│   ├── config.py            # 运行参数（全部支持环境变量覆盖）
│   ├── errors.py            # 统一异常：invalid_instruction / invalid_param
│   ├── bootstrap.py         # 依赖组装（API / CLI / 测试共用）
│   └── tools/
│       ├── base.py          # ToolCaller：超时、transient 重试、幂等缓存
│       ├── robot_execute.py # Mock 机器人：随机超时 / 硬件报错 / 参数拒绝
│       └── chem_prediction.py # Mock 化学模型
├── tests/
│   ├── conftest.py          # 统一 fixture（临时数据目录、零故障机器人）
│   ├── constants.py         # 三条示例指令
│   ├── unit/                # 单元测试：解析、校验、工具层、执行器分支
│   ├── integration/         # 集成测试：执行器 + 真实 Mock 工具 + 文件落盘
│   └── e2e/                 # 端到端测试：HTTP 接口全链路
├── examples/
│   ├── demo.py              # 三个演示场景
│   └── requests/            # curl 用请求体（含中文，避免终端编码问题）
├── scripts/
│   ├── e2e_curl.py          # 进程级 E2E：起 uvicorn + curl 打接口，指令作为脚本参数
│   └── e2e_docker.py        # 容器级 E2E：docker run 起容器 + curl 打接口，指令作为脚本参数
├── data/runs/               # 运行时生成，一次实验一个 JSON
├── requirements.txt
├── pytest.ini
├── Dockerfile               # python:3.12-slim，内置 curl，带 HEALTHCHECK
├── .dockerignore
└── README.md
```

## 启动

```bash
pip install -r requirements.txt

# 1) HTTP 服务
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 2) 命令行（不启服务也能演示）
python -m app.cli "配置 0.1mol/L 的乙酸乙酯溶液，取 2ml，升温到 45℃，搅拌 30 分钟，然后取样检测，若产率低于 60%，则重新调整温度到 50℃再重复一轮；如果产率≥60%，实验结束，生成实验记录。"

# 3) 三个场景一次跑完
python examples/demo.py
```

## Docker

镜像名 / 容器名统一用仓库名 `test-agent`。镜像基于 `python:3.12-slim`，装 `curl` 供健康检查与 E2E 脚本使用；记录目录 `/app/data/runs` 由 `CHEM_DATA_DIR` 指定。

```bash
# 构建
docker build -t test-agent:demo .

# 起服务（挂载本地 data 目录，记录落盘到宿主机）
docker run -d --rm --name test-agent -p 8000:8000 -v "${PWD}/data:/app/data" test-agent:demo

# 宿主机 curl 验证
curl -s http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/api/experiments \
  -H "Content-Type: application/json" --data-binary @examples/requests/success.json

# 健康状态 / 停止
docker ps --filter name=test-agent
docker stop test-agent
```

容器内跑测试（不需要起服务，镜像里已包含全部源码与依赖）：

```bash
# 全部 42 个 pytest 用例
docker run --rm test-agent:demo python -m pytest

# 进程级 E2E：容器内起 uvicorn + 容器内 curl
docker run --rm test-agent:demo python scripts/e2e_curl.py "配置 0.1mol/L 的乙酸乙酯溶液，取 2ml，升温到 45℃，搅拌 30 分钟，然后取样检测，若产率低于 60%，则重新调整温度到 50℃再重复一轮；如果产率≥60%，实验结束，生成实验记录。" --fault-rate 0.3 --seed 2

# 三个演示场景（含中文输出，需 UTF-8）
docker run --rm -e PYTHONUTF8=1 test-agent:demo python examples/demo.py
```

> Windows 下 `-v "${PWD}/data:/app/data"` 用 `${PWD}`；PowerShell 里请写成 `"${PWD}\data:/app/data"`。

## 接口 curl 示例

```bash
curl http://127.0.0.1:8000/health

# 成功闭环（默认 Mock 故障率 0.3，靠重试 + 幂等走完）
curl -X POST http://127.0.0.1:8000/api/experiments \
  -H "Content-Type: application/json" \
  --data-binary @examples/requests/success.json

# 到达轮次上限（阈值 80%，两轮都不达标）
curl -X POST http://127.0.0.1:8000/api/experiments \
  -H "Content-Type: application/json" \
  --data-binary @examples/requests/max_rounds.json

# 参数非法，调用工具前被拦截（HTTP 422）
curl -X POST http://127.0.0.1:8000/api/experiments \
  -H "Content-Type: application/json" \
  --data-binary @examples/requests/invalid_param.json

# 查询已落盘的实验记录
curl http://127.0.0.1:8000/api/experiments/<run_id>
```

> 也支持内联 JSON：`--data '{"instruction":"..."}'`。Windows 终端直接写中文易乱码，推荐使用 `--data-binary @file`。

请求体可选覆盖运行参数：

```json
{
  "instruction": "...",
  "options": {"robot_fault_rate": 0.5, "robot_seed": 7, "max_rounds": 2}
}
```

## 模块职责

| 模块 | 职责 | 不负责 |
|---|---|---|
| `parser.py` | 自然语言 → 结构化计划（浓度/体积/温度/时长/阈值/轮次/反应物） | 不做流程控制，不做参数放行判断 |
| `planner.py` | 动作白名单、参数名白名单、数值范围、阈值范围、轮次硬上限 | 不执行、不重试 |
| `executor.py` | 顺序执行、产率分支、轮次上限、步骤/总时限预算、生成记录 | 不解析自然语言、不直接关心硬件细节 |
| `tools/base.py` | 统一超时、错误三分类、transient 重试上限、幂等缓存 | 不判断业务条件 |
| `tools/robot_execute.py` | 模拟移液/加热/搅拌/取样，随机超时、硬件报错、参数拒绝 | 不做业务重试 |
| `tools/chem_prediction.py` | Mock 产率模型（温度升高产率上升） | 不做真实化学计算 |
| `store.py` | 每步原子写盘，支持按 run_id 读取 | 无数据库、无并发控制 |
| `main.py` | HTTP 契约、参数覆盖、错误码映射 | 不含业务编排 |

## P0 约束落地位置

| 约束 | 落地 |
|---|---|
| 解析结果可校验 | `parser` 输出 `ExperimentPlan`，`planner.validate_plan` 统一校验，失败返回 422 |
| 确定性执行 | `executor._drive` 顺序执行，分支由 `_OPS` 代码比较，LLM 不参与 |
| 超时 + 错误分类 | `ToolCaller._with_timeout`；`transient` / `hardware` / `invalid_param` |
| 只重试 transient | `ToolCaller.call` 遇到非 transient 立即失败；重试上限 `TOOL_MAX_RETRIES` |
| 幂等，不重复物理动作 | 幂等 key = `run_id:round:step_id`；`ToolCaller` 缓存 + 机器人侧 `applied` 台账 |
| 循环硬上限 | `MAX_ROUNDS`，超出直接压到上限并记 warning |
| 参数白名单 | `planner.validate_action`：未知动作/未知参数/越界值全部在调用前拦截 |
| 全链路预算 | `TOTAL_TIMEOUT_S`、`MAX_STEPS`，超限即终止并落盘 |
| 每步可追溯 | `executor` 每步 `store.save`，记录 attempts / duration / duplicate_guard / error |
| 演示可复现 | 固定随机种子 `ROBOT_SEED` + 可配故障率 `ROBOT_FAULT_RATE` |

## 测试

```bash
python -m pytest                 # 全部（42 个用例）
python -m pytest tests/unit       # 单元测试
python -m pytest tests/integration # 集成测试
python -m pytest tests/e2e        # 端到端测试
```

| 分层 | 覆盖点 |
|---|---|
| 单元 `tests/unit` | 指令解析与缺参报错、单位变体、轮次压上限；动作/参数白名单；重试只发生在 transient、超时分类、幂等缓存；执行器首轮成功/回跳成功/轮次上限/硬件失败/步骤预算 |
| 集成 `tests/integration` | 执行器 + 真实 Mock 工具 + 文件存储：每步落盘、transient 恢复、超时后设备侧幂等（物理动作不重复）、记录可重新加载 |
| E2E `tests/e2e` | HTTP 全链路：成功闭环、到达轮次上限、参数非法 422、无法解析 422、`max_rounds` 覆盖、记录查询 404 |
| 进程级 E2E `scripts/e2e_curl.py` | 真 uvicorn 子进程 + 真 curl：进程启动、端口、真实 HTTP 编解码、记录落盘、GET 回读 |

### 进程级 / 容器级 E2E 脚本（自然语言指令作为参数）

`tests/e2e/test_api.py` 用 TestClient 进程内直调 ASGI；下面两个脚本发真实 HTTP 请求，指令都是脚本的第一个参数。

```powershell
# A) 宿主机直起 uvicorn 进程
python scripts\e2e_curl.py "配置 0.1mol/L 的乙酸乙酯溶液，取 2ml，升温到 45℃，搅拌 30 分钟，然后取样检测，若产率低于 60%，则重新调整温度到 50℃再重复一轮；如果产率≥60%，实验结束，生成实验记录。"

# B) docker run 起容器（自动选端口、自动清理容器）
python scripts\e2e_docker.py "配置 0.1mol/L 的乙酸乙酯溶液，取 2ml，升温到 45℃，搅拌 30 分钟，然后取样检测，若产率低于 60%，则重新调整温度到 50℃再重复一轮；如果产率≥60%，实验结束，生成实验记录。"

# 常用变体
python scripts\e2e_docker.py "<把阈值改成 80% 的指令>" --expect max_rounds_reached
python scripts\e2e_docker.py "<把 2ml 改成 500ml 的指令>" --expect invalid_param
python scripts\e2e_docker.py "<成功指令>" --fault-rate 0.3 --seed 2   # 演示超时重试 + 幂等
python scripts\e2e_docker.py "<成功指令>" --build                     # 先重建镜像
python scripts\e2e_docker.py "<成功指令>" --keep-container            # 保留容器排查
```

两个脚本都自动选空闲端口，退出码 `0` 通过 / `1` 断言失败 / `2` 启动或环境错误。容器级脚本额外校验记录落在容器内 `/app/data/runs/`。

## 示例运行输出

### 场景 1：成功闭环（Mock 机器人 30% 故障率，重试 + 幂等兜底）

```
run_id: exp-20261028122306-568cc3  status: success
阈值: 60%  轮次上限: 2  实际轮次: 2
------------------------------------------------------------------------------
R1  s1  移液配制   robot_execute   success     0ms  attempts=1
R1  s2  升温     robot_execute   success     0ms  attempts=1
R1  s3  搅拌     robot_execute   success  1002ms  attempts=2  [幂等命中]
R1  s4  取样     robot_execute   success  1001ms  attempts=2  [幂等命中]
R1  s5  产率检测   chem_prediction success     0ms  attempts=1
R2  r1  复温调整   robot_execute   success     0ms  attempts=1
R2  r2  搅拌     robot_execute   success     0ms  attempts=1
R2  r3  取样     robot_execute   success     0ms  attempts=1
R2  r4  产率检测   chem_prediction success     0ms  attempts=1
------------------------------------------------------------------------------
产率轨迹: [0.55, 0.65]
发生重试: ['s3', 's4']   幂等保护: ['s3', 's4']
结论: 实验成功：第 2 轮产率 65.0% ≥ 阈值 60%
```

`attempts=2` 表示一次超时后重试；`[幂等命中]` 表示重试拿到的是设备侧台账结果，移液/搅拌没有被下发第二次。

### 场景 2：到达轮次上限（阈值 80%）

```
status: max_rounds_reached
阈值: 80%  轮次上限: 2  实际轮次: 2
产率轨迹: [0.55, 0.65]
结论: 达到轮次上限 2：最终产率 65.0% < 阈值 80%，需人工介入
```

### 场景 3：参数非法拒绝（取 500ml）

```json
{
  "code": "invalid_param",
  "message": "参数 volume_ml=500.0 超出允许范围 (0.0, 100.0]",
  "detail": {"param": "volume_ml", "value": 500.0, "allowed": "(0.0, 100.0]"}
}
```

### 成功响应（节选）

```json
{
  "code": 0,
  "data": {
    "run_id": "exp-20261028122245-b62744",
    "status": "success",
    "result": {"final_yield_rate": 0.65, "yield_history": [0.55, 0.65], "rounds_executed": 2, "passed": true},
    "record": {
      "conclusion": "实验成功：第 2 轮产率 65.0% ≥ 阈值 60%",
      "steps_total": 9,
      "steps_failed": 0,
      "retried_steps": ["s3", "s4"],
      "duplicate_guarded": ["s3", "s4"]
    }
  }
}
```

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TOOL_TIMEOUT_S` | `1.0` | 单次工具调用超时 |
| `TOOL_MAX_RETRIES` | `2` | transient 错误重试次数（不含首次） |
| `MAX_ROUNDS` | `3` | 总轮次硬上限（含首轮） |
| `TOTAL_TIMEOUT_S` | `60` | 单次实验总时限 |
| `MAX_STEPS` | `20` | 单次实验最大步骤数 |
| `ROBOT_FAULT_RATE` | `0.3` | Mock 机器人故障率，0 表示完全确定 |
| `ROBOT_SEED` | `2` | 故障随机种子，固定后演示可复现 |
| `CHEM_DATA_DIR` | `data/runs` | 记录落盘目录 |

## 边界限制（Demo 不支持）

- 单条指令、单反应体系、单反应器；不支持并发实验、多设备调度。
- 控制流仅「顺序步骤 + 单一阈值回跳」，不支持任意嵌套循环、并行分支、异常化学处理。
- 解析器为规则实现，只覆盖浓度/体积/温度/搅拌时长/产率阈值/重复轮次这类表达；无 LLM 调用、无多轮追问；缺参直接 422。
- `chem_prediction` 与 `robot_execute` 均为 Mock，不做真实配方可行性、安全性、热力学校验。
- 参数为固定白名单枚举，不做通用单位换算库（仅识别 `mol/L`、`ml/mL/毫升`、`℃/°C/度`、`分钟/min`）。
- 无数据库、无鉴权、无多租户；记录为本地 JSON，不支持跨进程恢复、断电续跑。
- 工具超时通过线程实现，超时线程可能仍在后台运行（Demo 取舍），不适用于真实硬件长任务。
- 无 UI，仅 CLI + HTTP；不做实时进度推送（同步返回完整结果）。

## 扩展方向

- 把 `parser.parse_instruction` 换成 LLM 结构化输出，保持 `ExperimentPlan` 契约与 `validate_plan` 不变。
- `ExperimentPlan` 从「首轮 + 重复轮」扩展为带显式跳转的步骤图，支持多分支控制流。
- 记录层换 SQLite，支持 run 列表、断点恢复；执行器增加事件回调用于实时进度。
