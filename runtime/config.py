"""Configuration and environment management for the tool-calling runtime."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RuntimeConfig:
    # LLM Provider settings
    llm_base_url: str = field(
        default_factory=lambda: os.getenv("LLM_BASE_URL", "http://127.0.0.1:20128/v1")
    )
    llm_api_key: str = field(
        default_factory=lambda: os.getenv("LLM_API_KEY", "dummy-key")
    )
    llm_model: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL", "go/chatgpt-web")
    )

    # Execution boundaries
    workspace_dir: Path = field(
        default_factory=lambda: Path(os.getenv("WORKSPACE_DIR", "./workspace")).resolve()
    )
    max_turns: int = field(
        default_factory=lambda: int(os.getenv("MAX_TURNS", "15"))
    )
    default_timeout: float = field(
        default_factory=lambda: float(os.getenv("TOOL_TIMEOUT", "30.0"))
    )
    max_output_bytes: int = field(
        default_factory=lambda: int(os.getenv("MAX_OUTPUT_BYTES", "32768"))  # 32 KB limit
    )

    # State & Audit persistence
    audit_log_path: Path = field(
        default_factory=lambda: Path(os.getenv("AUDIT_LOG_PATH", "./audit.jsonl")).resolve()
    )
    session_db_path: Path = field(
        default_factory=lambda: Path(os.getenv("SESSION_DB_PATH", "./sessions.db")).resolve()
    )

    # Security settings
    blocked_commands: set[str] = field(
        default_factory=lambda: {
            "rm -rf /",
            "rm -rf /*",
            ":(){ :|:& };:",
            "mkfs",
            "dd if=/dev/zero",
            "dd if=/dev/urandom",
            "> /dev/sda",
            "shutdown",
            "reboot",
            "init 0",
        }
    )

    sensitive_env_keys: set[str] = field(
        default_factory=lambda: {
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "LLM_API_KEY",
            "HERMES_PEER_KEY",
            "API_SERVER_KEY",
            "SSHPASS",
            "AWS_SECRET_ACCESS_KEY",
            "VAULT_TOKEN",
            "GITHUB_TOKEN",
            "GH_TOKEN",
        }
    )

    def ensure_directories(self) -> None:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_db_path.parent.mkdir(parents=True, exist_ok=True)
