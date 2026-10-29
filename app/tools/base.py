"""工具调用层：统一超时、错误分类、重试上限、幂等缓存。"""

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, Callable


class ToolError(Exception):
    code = "tool_error"
    transient = False  # 只有 transient 错误允许重试

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ToolTimeout(ToolError):
    code = "timeout"
    transient = True


class ToolBusy(ToolError):
    code = "device_busy"
    transient = True


class HardwareError(ToolError):
    code = "hardware_error"
    transient = False


class ParamRejected(ToolError):
    code = "invalid_param"
    transient = False


@dataclass
class ToolResult:
    ok: bool
    value: dict | None = None
    attempts: int = 0
    duration_ms: int = 0
    duplicate: bool = False
    error_code: str | None = None
    error_message: str | None = None


class ToolCaller:
    def __init__(self, *, timeout_s: float, max_retries: int):
        self.timeout_s = timeout_s
        self.max_retries = max(0, max_retries)
        self._cache: dict[str, dict] = {}

    def call(self, tool: str, fn: Callable[[], dict], idempotency_key: str) -> ToolResult:
        if idempotency_key in self._cache:
            # 同一物理动作已成功过，直接复用结果，绝不重复下发
            return ToolResult(ok=True, value=self._cache[idempotency_key], attempts=0, duplicate=True)

        started = time.perf_counter()
        attempts = 0
        last: ToolError | None = None
        while attempts <= self.max_retries:
            attempts += 1
            try:
                value = self._with_timeout(fn)
                self._cache[idempotency_key] = value
                return ToolResult(
                    ok=True,
                    value=value,
                    attempts=attempts,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            except ToolError as exc:
                last = exc
                if not exc.transient:
                    break
            except Exception as exc:  # 未知异常按不可重试处理，避免扩散
                last = ToolError(f"{tool} 未预期异常: {exc}")
                break

        return ToolResult(
            ok=False,
            attempts=attempts,
            duration_ms=int((time.perf_counter() - started) * 1000),
            error_code=last.code if last else "tool_error",
            error_message=last.message if last else "unknown error",
        )

    def _with_timeout(self, fn: Callable[[], dict]) -> dict:
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(fn)
        try:
            return future.result(timeout=self.timeout_s)
        except FutureTimeout as exc:
            raise ToolTimeout(f"工具调用超时 (>{self.timeout_s:g}s)") from exc
        finally:
            pool.shutdown(wait=False)
