#!/usr/bin/env python
"""进程级 E2E：起真实 uvicorn 子进程，用真实 curl 打接口，指令从命令行参数传入。

与 tests/e2e/test_api.py 的区别：那边是 TestClient 进程内直调 ASGI，
本脚本起独立进程、占真实端口、发真实 HTTP 请求。

用法：
  python scripts/e2e_curl.py "配置 0.1mol/L 的乙酸乙酯溶液，取 2ml，升温到 45℃，搅拌 30 分钟，然后取样检测，若产率低于 60%，则重新调整温度到 50℃再重复一轮；如果产率≥60%，实验结束，生成实验记录。"

  # 到达轮次上限
  python scripts/e2e_curl.py "配置 0.1mol/L 的乙酸乙酯溶液，取 2ml，升温到 45℃，搅拌 30 分钟，取样检测，产率低于 80% 时升温到 50℃重复一轮。" --expect max_rounds_reached

  # 参数非法，调用工具前被拦截
  python scripts/e2e_curl.py "配置 0.1mol/L 的乙酸乙酯溶液，取 500ml，升温到 45℃，搅拌 30 分钟，取样检测，产率低于 60% 时重复一轮。" --expect invalid_param

  # 30% 故障率下演示超时重试 + 幂等（种子固定，可复现）
  python scripts/e2e_curl.py "<成功指令>" --fault-rate 0.3 --seed 2

退出码：0 通过 / 1 断言失败 / 2 启动或环境错误
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTATIONS = {
    "success": {"http": 200, "run_status": "success"},
    "max_rounds_reached": {"http": 200, "run_status": "max_rounds_reached"},
    "failed": {"http": 200, "run_status": "failed"},
    "invalid_param": {"http": 422, "code": "invalid_param"},
    "invalid_instruction": {"http": 422, "code": "invalid_instruction"},
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="uvicorn + curl 进程级 E2E")
    parser.add_argument("instruction", help="自然语言实验指令（整句用引号包住）")
    parser.add_argument("--expect", default="success", choices=sorted(EXPECTATIONS))
    parser.add_argument("--port", type=int, default=0, help="监听端口，0 = 自动选空闲端口")
    parser.add_argument("--fault-rate", type=float, default=0.0, help="Mock 机器人故障率，0 = 完全确定")
    parser.add_argument("--seed", type=int, default=2, help="故障随机种子，固定后结果可复现")
    parser.add_argument("--tool-timeout", type=float, default=1.0)
    parser.add_argument("--ready-timeout", type=float, default=30.0)
    parser.add_argument("--keep-data", action="store_true", help="保留临时目录（记录文件 + 日志）")
    parser.add_argument("--json", action="store_true", help="打印原始响应 JSON")
    return parser.parse_args(argv)


def find_curl() -> str:
    for name in ("curl.exe", "curl"):
        path = shutil.which(name)
        if path:
            return path
    raise SystemExit("[FAIL] 未找到 curl 可执行文件")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def curl(curl_cmd: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([curl_cmd, *args], capture_output=True, timeout=60)


def start_uvicorn(port: int, env: dict, log_path: Path):
    log_file = log_path.open("wb")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    return proc, log_file


def stop_uvicorn(proc: subprocess.Popen, log_file) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    if not log_file.closed:
        log_file.close()


def wait_ready(curl_cmd: str, base: str, proc: subprocess.Popen, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn 提前退出，exit code = {proc.returncode}")
        result = curl(curl_cmd, "-s", "-f", f"{base}/health")
        if result.returncode == 0 and b'"ok"' in result.stdout:
            return
        time.sleep(0.2)
    raise RuntimeError(f"等待 /health 就绪超时 ({timeout:g}s)")


def verify(args, http_status: int, body: dict, record_exists) -> list[str]:
    """record_exists(run_id) -> bool：由调用方决定从哪查记录（本地文件 / 容器内文件）。"""
    spec = EXPECTATIONS[args.expect]
    fails: list[str] = []

    if http_status != spec["http"]:
        fails.append(f"HTTP 状态码期望 {spec['http']}，实际 {http_status}")

    if "run_status" in spec:
        if body.get("code") != 0:
            fails.append(f"响应 code 期望 0，实际 {body.get('code')}")
        data = body.get("data") or {}
        if data.get("status") != spec["run_status"]:
            fails.append(f"run.status 期望 {spec['run_status']}，实际 {data.get('status')}")
        run_id = data.get("run_id") or ""
        if not run_id:
            fails.append("响应缺少 run_id")
        elif not record_exists(run_id):
            fails.append(f"记录文件未落盘: {run_id}.json")
        if not (data.get("record") or {}).get("conclusion"):
            fails.append("record.conclusion 缺失")
    else:
        if body.get("code") != spec["code"]:
            fails.append(f"错误码期望 {spec['code']}，实际 {body.get('code')}")
        if not body.get("message"):
            fails.append("错误响应缺少 message")
    return fails


def print_result(http_status: int, body: dict, fails: list[str]) -> None:
    print("-" * 72)
    print(f"HTTP : {http_status}")
    data = body.get("data") or {}
    if data:
        print(f"run_id : {data.get('run_id')}")
        print(f"status : {data.get('status')}")
        rec = data.get("record") or {}
        if rec.get("conclusion"):
            print(f"结论   : {rec['conclusion']}")
        if rec.get("yield_history"):
            print(f"产率   : {rec['yield_history']}")
        steps = data.get("steps") or []
        print(f"步骤   : 共 {len(steps)} 步")
        for step in steps:
            extra = f" attempts={step['attempts']}" if step.get("attempts", 1) > 1 else ""
            dup = " [幂等命中]" if step.get("duplicate_guard") else ""
            print(
                f"  R{step['round']} {step['step_id']} {step['name']} "
                f"{step['tool']} {step['status']}{extra}{dup}"
            )
    else:
        print(f"code    : {body.get('code')}")
        print(f"message : {body.get('message')}")
    print("-" * 72)
    for item in fails:
        print(f"[FAIL] {item}")
    if not fails:
        print("[PASS]")


def print_log_tail(log_path: Path, lines: int = 20) -> None:
    if not log_path.exists():
        return
    text = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    if text:
        print("--- uvicorn 日志尾部 ---")
        for line in text[-lines:]:
            print(line)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    args = parse_args(argv)
    curl_cmd = find_curl()
    port = args.port or free_port()
    base = f"http://127.0.0.1:{port}"

    workdir = Path(tempfile.mkdtemp(prefix="chem-e2e-"))
    data_dir = workdir / "runs"
    req_path = workdir / "request.json"
    resp_path = workdir / "response.json"
    log_path = workdir / "uvicorn.log"
    # 中文走文件、不走 curl 命令行，规避 Windows 参数编码问题
    req_path.write_text(
        json.dumps({"instruction": args.instruction}, ensure_ascii=False), encoding="utf-8"
    )

    env = os.environ.copy()
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "CHEM_DATA_DIR": str(data_dir),
            "ROBOT_FAULT_RATE": str(args.fault_rate),
            "ROBOT_SEED": str(args.seed),
            "TOOL_TIMEOUT_S": str(args.tool_timeout),
        }
    )

    proc = None
    log_file = None
    try:
        print(f"[1/5] 启动 uvicorn -> {base}")
        proc, log_file = start_uvicorn(port, env, log_path)

        print(f"[2/5] 等待 /health 就绪（timeout={args.ready_timeout:g}s）")
        wait_ready(curl_cmd, base, proc, args.ready_timeout)

        print(f"[3/5] curl POST {base}/api/experiments")
        poster = curl(
            curl_cmd, "-s", "-o", str(resp_path), "-w", "%{http_code}",
            "-X", "POST", f"{base}/api/experiments",
            "-H", "Content-Type: application/json",
            "--data-binary", f"@{req_path}",
        )
        if poster.returncode != 0:
            raise RuntimeError(f"curl 请求失败 (exit={poster.returncode})")
        http_status = int(poster.stdout.decode().strip() or 0)
        body = json.loads(resp_path.read_text(encoding="utf-8"))

        print(f"[4/5] 校验响应（--expect={args.expect}）")
        fails = verify(
            args, http_status, body, lambda rid: (data_dir / f"{rid}.json").exists()
        )

        run_id = ""
        if isinstance(body.get("data"), dict):
            run_id = body["data"].get("run_id", "")
        if run_id:
            print(f"[5/5] curl GET {base}/api/experiments/{run_id} 回读记录")
            getter = curl(curl_cmd, "-s", "-f", f"{base}/api/experiments/{run_id}")
            if getter.returncode != 0:
                fails.append("GET 回读记录失败")
            else:
                loaded = json.loads(getter.stdout.decode("utf-8"))
                if (loaded.get("data") or {}).get("run_id") != run_id:
                    fails.append("GET 回读的 run_id 不一致")
        else:
            print("[5/5] 无 run_id（拦截场景），跳过回读")

        if args.json:
            print(json.dumps(body, ensure_ascii=False, indent=2))
        print_result(http_status, body, fails)
        print("等价手工命令：")
        print("  python -m uvicorn app.main:app --port 8000")
        print("  curl -s http://127.0.0.1:8000/health")
        print(
            '  curl -s -X POST http://127.0.0.1:8000/api/experiments '
            '-H "Content-Type: application/json" --data-binary @examples/requests/success.json'
        )
        return 0 if not fails else 1

    except RuntimeError as exc:
        print(f"[ERROR] {exc}")
        print_log_tail(log_path)
        return 2
    finally:
        if proc is not None and log_file is not None:
            stop_uvicorn(proc, log_file)
        if args.keep_data:
            print(f"临时目录保留: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
