"""统一异常：API 层按 code 返回 4xx，避免把校验失败暴露成 500。"""


class AgentError(Exception):
    code = "agent_error"
    http_status = 422

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class InstructionParseError(AgentError):
    """自然语言无法解析成实验计划。"""

    code = "invalid_instruction"


class PlanInvalidError(AgentError):
    """计划中有非法动作或越界参数，调用工具前拦截。"""

    code = "invalid_param"
