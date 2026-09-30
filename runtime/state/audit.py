"""Structured append-only audit logging for all tool executions."""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("runtime.audit")


@dataclass
class AuditRecord:
    timestamp: float
    session_id: str
    tool_name: str
    arguments: dict[str, Any]
    success: bool
    execution_time_s: float
    output_length: int
    error: str | None = None


class AuditLogger:
    def __init__(self, log_path: Path):
        self.log_path = Path(log_path)
        self._lock = threading.Lock()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        success: bool,
        execution_time_s: float,
        output_length: int,
        error: str | None = None,
    ) -> None:
        # Sanitize sensitive values in logged arguments
        sanitized_args = {}
        for k, v in arguments.items():
            if any(s in k.lower() for s in ["key", "pass", "token", "secret"]):
                sanitized_args[k] = "***REDACTED***"
            else:
                sanitized_args[k] = v

        record = AuditRecord(
            timestamp=time.time(),
            session_id=session_id,
            tool_name=tool_name,
            arguments=sanitized_args,
            success=success,
            execution_time_s=round(execution_time_s, 4),
            output_length=output_length,
            error=error,
        )

        line = json.dumps(asdict(record)) + "\n"
        with self._lock:
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception as e:
                logger.error(f"Failed to write audit log: {e}")
