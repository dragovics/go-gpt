"""Terminal execution tool with timeout, safe env, and command validation."""
from __future__ import annotations

import asyncio
from typing import Any

from ..config import RuntimeConfig
from ..safety import check_command_safety, sanitize_env, truncate_output
from .base import BaseTool


class TerminalTool(BaseTool):
    name = "terminal"
    description = (
        "Execute a bash shell command in the workspace. Returns stdout, stderr, and exit code. "
        "Destructive commands and dangerous patterns are strictly blocked by safety policy."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in seconds (default: 30).",
            },
        },
        "required": ["command"],
    }

    def __init__(self, config: RuntimeConfig):
        self.config = config

    async def execute(self, **kwargs: Any) -> str:
        command = kwargs.get("command", "")
        timeout = float(kwargs.get("timeout") or self.config.default_timeout)

        # 1. Safety check
        check_command_safety(command, self.config.blocked_commands)

        # 2. Sanitize environment
        env = sanitize_env(self.config.sensitive_env_keys)

        # 3. Execute subprocess
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
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
                return f"[TIMEOUT] Command '{command}' timed out after {timeout} seconds."

            out = stdout_b.decode("utf-8", errors="replace")
            err = stderr_b.decode("utf-8", errors="replace")
            code = proc.returncode

            combined = []
            if out:
                combined.append(out)
            if err:
                combined.append(f"[STDERR]\n{err}")
            if code != 0:
                combined.append(f"[Process exited with code {code}]")

            res_text = "\n".join(combined).strip() or "[Command executed successfully with no output]"
            return truncate_output(res_text, self.config.max_output_bytes)

        except Exception as e:
            return f"[ERROR] Failed to execute command: {type(e).__name__}: {str(e)}"
