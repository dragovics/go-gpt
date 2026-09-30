"""File system operations strictly confined to the workspace boundary."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..config import RuntimeConfig
from ..safety import check_path_confinement, truncate_output
from .base import BaseTool


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read UTF-8 text from a file inside the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative file path inside workspace."}
        },
        "required": ["path"],
    }

    def __init__(self, config: RuntimeConfig):
        self.config = config

    async def execute(self, **kwargs: Any) -> str:
        rel_path = kwargs.get("path", "")
        resolved = check_path_confinement(rel_path, self.config.workspace_dir)
        if not resolved.exists():
            return f"[ERROR] File not found: '{rel_path}'"
        if not resolved.is_file():
            return f"[ERROR] Path is not a regular file: '{rel_path}'"
        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
            return truncate_output(content, self.config.max_output_bytes)
        except Exception as e:
            return f"[ERROR] Could not read file: {e}"


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write text content to a file inside the workspace. Creates parent directories automatically."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative file path inside workspace."},
            "content": {"type": "string", "description": "Text content to write."},
        },
        "required": ["path", "content"],
    }

    def __init__(self, config: RuntimeConfig):
        self.config = config

    async def execute(self, **kwargs: Any) -> str:
        rel_path = kwargs.get("path", "")
        content = kwargs.get("content", "")
        resolved = check_path_confinement(rel_path, self.config.workspace_dir)
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            return f"[OK] Successfully wrote {len(content)} characters to '{rel_path}'"
        except Exception as e:
            return f"[ERROR] Could not write file: {e}"


class ListDirTool(BaseTool):
    name = "list_dir"
    description = "List entries in a directory inside the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Optional relative directory path (default: current workspace root)."}
        },
    }

    def __init__(self, config: RuntimeConfig):
        self.config = config

    async def execute(self, **kwargs: Any) -> str:
        rel_path = kwargs.get("path", "") or "."
        resolved = check_path_confinement(rel_path, self.config.workspace_dir)
        if not resolved.exists():
            return f"[ERROR] Directory not found: '{rel_path}'"
        if not resolved.is_dir():
            return f"[ERROR] Path is not a directory: '{rel_path}'"
        try:
            entries = []
            for item in sorted(resolved.iterdir()):
                prefix = "[DIR]" if item.is_dir() else "[FILE]"
                entries.append(f"{prefix} {item.name}")
            out = "\n".join(entries) if entries else "[Directory is empty]"
            return truncate_output(out, self.config.max_output_bytes)
        except Exception as e:
            return f"[ERROR] Could not list directory: {e}"
