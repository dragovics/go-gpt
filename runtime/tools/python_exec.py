"""Python code execution tool with isolated subprocess runtime."""
from __future__ import annotations

import asyncio
import sys
from typing import Any

from ..config import RuntimeConfig
from ..safety import sanitize_env, truncate_output
from .base import BaseTool


class PythonExecTool(BaseTool):
    name = "execute_code"
    description = (
        "Execute Python 3 code in an isolated subprocess. Returns captured stdout and stderr. "
        "Useful for computational logic, data analysis, or script evaluation."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Valid Python 3 source code to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in seconds (default: 30).",
            },
        },
        "required": ["code"],
    }

    def __init__(self, config: RuntimeConfig):
        self.config = config

    async def execute(self, **kwargs: Any) -> str:
        code = kwargs.get("code", "")
        timeout = float(kwargs.get("timeout") or self.config.default_timeout)

        env = sanitize_env(self.config.sensitive_env_keys)
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-u",
                "-c",
                code,
                cwd=str(self.config.workspace_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
                return f"[TIMEOUT] Python execution timed out after {timeout} seconds."

            out = stdout_b.decode("utf-8", errors="replace")
            err = stderr_b.decode("utf-8", errors="replace")
            code_exit = proc.returncode

            combined = []
            if out:
                combined.append(out)
            if err:
                combined.append(f"[STDERR]\n{err}")
            if code_exit != 0:
                combined.append(f"[Python process exited with code {code_exit}]")

            res_text = "\n".join(combined).strip() or "[Execution completed with no output]"
            return truncate_output(res_text, self.config.max_output_bytes)

        except Exception as e:
            return f"[ERROR] Execution failed: {type(e).__name__}: {str(e)}"
