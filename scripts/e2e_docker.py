#!/usr/bin/env python
"""Docker 级 E2E：docker run 起容器 → 宿主机 curl 打真实端口 → 校验 → 清理容器。

与 scripts/e2e_curl.py 的区别：那边在宿主机直接起 uvicorn 进程；
本脚本拉起的是容器，验证镜像可运行、端口映射、容器内记录落盘。

用法（自然语言指令作为第一个参数）：
  python scripts/e2e_docker.py "配置 0.1mol/L 的乙酸乙酯溶液，取 2ml，升温到 45℃，搅拌 30 分钟，然后取样检测，若产率低于 60%，则重新调整温度到 50℃再重复一轮；如果产率≥60%，实验结束，生成实验记录。"

  python scripts/e2e_docker.py "<阈值 80% 的指令>" --expect max_rounds_reached
  python scripts/e2e_docker.py "<取 500ml 的指令>" --expect invalid_param
  python scripts/e2e_docker.py "<成功指令>" --fault-rate 0.3 --seed 2
  python scripts/e2e_docker.py "<成功指令>" --build          # 先重建镜像
  python scripts/e2e_docker.py "<成功指令>" --keep-container # 保留容器排查

退出码：0 通过 / 1 断言失败 / 2 环境、镜像或容器启动错误
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

# 复用 e2e_curl.py 里的期望表、curl 封装与输出格式，避免两份逻辑漂移
sys.path.insert(0, str(Path(__file__).resolve().parent))

from e2e_curl import EXPECTATIONS, curl, find_curl, free_port, print_result  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = "test-agent:demo"
CONTAINER_PORT = 8000


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="docker run + curl 容器级 E2E")
    parser.add_argument("instruction", help="自然语言实验指令（整句用引号包住）")
    parser.add_argument("--expect", default="success", choices=sorted(EXPECTATIONS))
    parser.add_argument("--image", default=DEFAULT_IMAGE, help=f"镜像名，默认 {DEFAULT_IMAGE}")
    parser.add_argument("--build", action="store_true", help="先 docker build 再测试")
    parser.add_argument("--port", type=int, default=0, help="宿主机端口，0 = 自动选空闲端口")
    parser.add_argument("--fault-rate", type=float, default=0.0, help="Mock 机器人故障率，0 = 完全确定")
    parser.add_argument("--seed", type=int, default=2, help="故障随机种子，固定后结果可复现")
    parser.add_argument("--tool-timeout", type=float, default=1.0)
    parser.add_argument("--ready-timeout", type=float, default=60.0)
    parser.add_argument("--keep-container", action="store_true", help="保留容器（结束后打印排查命令）")
    parser.add_argument("--json", action="store_true", help="打印原始响应 JSON")
    return parser.parse_args(argv)


def docker(*args: str, timeout: float | None = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def ensure_docker_and_image(image: str, build: bool) -> None:
    probe = docker("info", "--format", "{{.ServerVersion}}")
    if probe.returncode != 0:
        raise RuntimeError("Docker daemon 未运行，请先启动 Docker Desktop")

    if build:
        print(f"       构建镜像 {image}（首次约 1-2 分钟）...")
        result = docker("build", "-t", image, str(ROOT), timeout=None)
        if result.returncode != 0:
            raise RuntimeError(f"docker build 失败:\n{(result.stderr or result.stdout).strip()[-800:]}")

    if docker("image", "inspect", image).returncode != 0:
        raise RuntimeError(f"镜像 {image} 不存在，先执行 docker build -t {image} . 或加 --build")


def start_container(image: str, name: str, host_port: int, envs: dict) -> None:
    args = ["run", "-d", "--rm", "--name", name, "-p", f"{host_port}:{CONTAINER_PORT}"]
    for key, value in envs.items():
        args += ["-e", f"{key}={value}"]
    args.append(image)
    result = docker(*args)
    if result.returncode != 0:
        raise RuntimeError(f"docker run 失败: {(result.stderr or result.stdout).strip()}")


def stop_container(name: str) -> None:
    docker("rm", "-f", name)


def container_running(name: str) -> bool:
    result = docker("inspect", "-f", "{{.State.Running}}", name)
    return result.returncode == 0 and result.stdout.strip() == "true"


def wait_ready(curl_cmd: str, base: str, name: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not container_running(name):
            raise RuntimeError("容器已退出，检查镜像 CMD 或端口占用")
        result = curl(curl_cmd, "-s", "-f", f"{base}/health")
        if result.returncode == 0 and b'"ok"' in result.stdout:
            return
        time.sleep(0.3)
    raise RuntimeError(f"等待 /health 就绪超时 ({timeout:g}s)")


def container_has_record(name: str, run_id: str) -> bool:
    """记录应落在容器内 /app/data/runs，验证镜像里的落盘路径正确。"""
    return docker("exec", name, "test", "-f", f"/app/data/runs/{run_id}.json").returncode == 0


def print_container_logs(name: str, lines: int = 30) -> None:
    result = docker("logs", "--tail", str(lines), name)
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if output:
        print(f"--- 容器日志尾部 ({name}) ---")
        print(output)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    args = parse_args(argv)
    curl_cmd = find_curl()
    host_port = args.port or free_port()
    base = f"http://127.0.0.1:{host_port}"
    name = f"test-agent-e2e-{uuid.uuid4().hex[:8]}"
    started = False
    workdir = Path(tempfile.mkdtemp(prefix="test-agent-docker-e2e-"))
    req_path = workdir / "request.json"
    resp_path = workdir / "response.json"
    # 中文走文件、不走 curl 命令行，规避 Windows 参数编码问题
    req_path.write_text(
        json.dumps({"instruction": args.instruction}, ensure_ascii=False), encoding="utf-8"
    )

    try:
        print(f"[1/6] 检查 Docker daemon 与镜像 {args.image}")
        ensure_docker_and_image(args.image, args.build)

        print(f"[2/6] docker run -d --name {name} -p {host_port}:{CONTAINER_PORT}")
        start_container(
            args.image,
            name,
            host_port,
            {
                "ROBOT_FAULT_RATE": args.fault_rate,
                "ROBOT_SEED": args.seed,
                "TOOL_TIMEOUT_S": args.tool_timeout,
            },
        )
        started = True

        print(f"[3/6] 等待容器 /health 就绪（timeout={args.ready_timeout:g}s）")
        wait_ready(curl_cmd, base, name, args.ready_timeout)

        print(f"[4/6] curl POST {base}/api/experiments")
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

        print(f"[5/6] 校验响应（--expect={args.expect}）")
        fails = verify_via_container(args, http_status, body, name)

        run_id = ""
        if isinstance(body.get("data"), dict):
            run_id = body["data"].get("run_id", "")
        if run_id:
            print(f"[6/6] curl GET {base}/api/experiments/{run_id} + 容器内落盘检查")
            getter = curl(curl_cmd, "-s", "-f", f"{base}/api/experiments/{run_id}")
            if getter.returncode != 0:
                fails.append("GET 回读记录失败")
            else:
                loaded = json.loads(getter.stdout.decode("utf-8"))
                if (loaded.get("data") or {}).get("run_id") != run_id:
                    fails.append("GET 回读的 run_id 不一致")
        else:
            print("[6/6] 无 run_id（拦截场景），跳过回读")

        if args.json:
            print(json.dumps(body, ensure_ascii=False, indent=2))
        print_result(http_status, body, fails)
        print(f"容器 : {name}  镜像 : {args.image}  端口 : {host_port}")
        print("等价手工命令：")
        print(f"  docker build -t {args.image} .")
        print(f"  docker run -d --rm --name {name} -p {host_port}:{CONTAINER_PORT} {args.image}")
        print(f'  curl -s -X POST http://127.0.0.1:{host_port}/api/experiments '
              '-H "Content-Type: application/json" --data-binary @examples/requests/success.json')
        return 0 if not fails else 1

    except RuntimeError as exc:
        print(f"[ERROR] {exc}")
        if started:
            print_container_logs(name)
        return 2
    finally:
        if started:
            if args.keep_container:
                print(f"容器保留: {name}（排查完执行 docker rm -f {name}）")
            else:
                stop_container(name)
        shutil.rmtree(workdir, ignore_errors=True)


def verify_via_container(args, http_status: int, body: dict, name: str) -> list[str]:
    """复用 e2e_curl.verify 的断言逻辑，记录存在性改为在容器内检查。"""
    from e2e_curl import verify

    return verify(args, http_status, body, lambda rid: container_has_record(name, rid))


if __name__ == "__main__":
    raise SystemExit(main())
