"""Tool registry, argument validation, and dispatch execution layer."""
from __future__ import annotations

import logging
import time
from typing import Any

from .config import RuntimeConfig
from .safety import SecurityViolation
from .state.audit import AuditLogger
from .tools.base import BaseTool
from .validator import ValidationError, validate_arguments

logger = logging.getLogger("runtime.dispatcher")


class ToolDispatcher:
    def __init__(self, config: RuntimeConfig, audit_logger: AuditLogger | None = None):
        self.config = config
        self.audit_logger = audit_logger or AuditLogger(config.audit_log_path)
        self._tools: dict[str, BaseTool] = {}

    def register_tool(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool
        logger.debug(f"Registered tool: {tool.name}")

    def get_schemas(self) -> list[dict[str, Any]]:
        """Returns the list of OpenAI function schemas for all registered tools."""
        return [tool.to_openai_schema() for tool in self._tools.values()]

    async def dispatch(self, name: str, arguments: dict[str, Any], session_id: str = "default") -> str:
        """Dispatches and executes a tool with schema validation and audit logging."""
        t0 = time.time()
        tool = self._tools.get(name)

        if not tool:
            err_msg = f"[ERROR] Unknown tool: '{name}'. Available tools: {list(self._tools.keys())}"
            self.audit_logger.log(
                session_id=session_id,
                tool_name=name,
                arguments=arguments,
                success=False,
                execution_time_s=time.time() - t0,
                output_length=len(err_msg),
                error="Unknown tool",
            )
            return err_msg

        # 1. Schema Validation
        try:
            validate_arguments(tool.parameters, arguments)
        except ValidationError as val_err:
            err_msg = f"[SCHEMA VALIDATION ERROR] {str(val_err)}"
            self.audit_logger.log(
                session_id=session_id,
                tool_name=name,
                arguments=arguments,
                success=False,
                execution_time_s=time.time() - t0,
                output_length=len(err_msg),
                error=str(val_err),
            )
            return err_msg

        # 2. Execution with Safety & Exception Handling
        try:
            output = await tool.execute(**arguments)
            elapsed = time.time() - t0
            self.audit_logger.log(
                session_id=session_id,
                tool_name=name,
                arguments=arguments,
                success=True,
                execution_time_s=elapsed,
                output_length=len(output),
            )
            return output

        except SecurityViolation as sec_err:
            err_msg = f"[SECURITY VIOLATION] {str(sec_err)}"
            elapsed = time.time() - t0
            self.audit_logger.log(
                session_id=session_id,
                tool_name=name,
                arguments=arguments,
                success=False,
                execution_time_s=elapsed,
                output_length=len(err_msg),
                error=str(sec_err),
            )
            return err_msg

        except Exception as exc:
            err_msg = f"[TOOL EXECUTION FAILED] {type(exc).__name__}: {str(exc)}"
            elapsed = time.time() - t0
            self.audit_logger.log(
                session_id=session_id,
                tool_name=name,
                arguments=arguments,
                success=False,
                execution_time_s=elapsed,
                output_length=len(err_msg),
                error=str(exc),
            )
            return err_msg
