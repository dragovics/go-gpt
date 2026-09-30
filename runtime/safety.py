"""Security, path confinement, command filtering, and output bounding."""
from __future__ import annotations

import os
import re
from pathlib import Path


class SecurityViolation(PermissionError):
    """Raised when a tool action violates runtime safety boundaries."""


def check_path_confinement(target_path: str | Path, workspace_root: Path) -> Path:
    """Ensures a path resolves strictly inside the workspace boundary (anti-directory traversal)."""
    workspace = workspace_root.resolve()
    target = Path(target_path)
    if not target.is_absolute():
        resolved = (workspace / target).resolve()
    else:
        resolved = target.resolve()

    try:
        resolved.relative_to(workspace)
    except ValueError:
        raise SecurityViolation(
            f"Access denied: path '{target_path}' escapes workspace boundary '{workspace}'"
        )
    return resolved


def check_command_safety(command: str, blocked_patterns: set[str]) -> None:
    """Validates that a shell command does not contain destructive patterns."""
    cmd_clean = command.strip().lower()

    for pattern in blocked_patterns:
        pat_clean = pattern.lower()
        if pat_clean in cmd_clean:
            raise SecurityViolation(
                f"Command execution blocked by safety policy: contains forbidden pattern '{pattern}'"
            )

    # Check dangerous redirection to raw block devices
    if re.search(r">\s*/dev/(?:sd[a-z]|nvme|vd[a-z]|null\b)", cmd_clean):
        if "> /dev/null" not in cmd_clean and "2> /dev/null" not in cmd_clean and ">/dev/null" not in cmd_clean:
            raise SecurityViolation("Command execution blocked: raw device redirection detected")


def sanitize_env(sensitive_keys: set[str]) -> dict[str, str]:
    """Strips master secrets and API tokens before spawning subprocesses."""
    safe = {}
    for k, v in os.environ.items():
        if k in sensitive_keys or any(s in k.lower() for s in ["vault", "secret", "private_key"]):
            continue
        safe[k] = v
    # Ensure standard safe defaults
    safe["PYTHONIOENCODING"] = "utf-8"
    return safe


def truncate_output(output: str, max_bytes: int = 32768) -> str:
    """Truncates output safely with a clear warning if it exceeds size budget."""
    if not output:
        return ""
    raw_bytes = output.encode("utf-8", errors="replace")
    if len(raw_bytes) <= max_bytes:
        return output

    head_bytes = raw_bytes[: max_bytes // 2]
    tail_bytes = raw_bytes[- (max_bytes // 2):]

    head_text = head_bytes.decode("utf-8", errors="replace")
    tail_text = tail_bytes.decode("utf-8", errors="replace")

    notice = f"\n\n... [OUTPUT TRUNCATED: {len(raw_bytes)} bytes exceeded budget {max_bytes} bytes] ...\n\n"
    return head_text + notice + tail_text
