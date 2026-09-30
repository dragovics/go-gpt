"""Tests for tool executors: terminal, python exec, and file operations."""
from __future__ import annotations

from pathlib import Path

import pytest

from runtime.config import RuntimeConfig
from runtime.safety import SecurityViolation
from runtime.tools.files import ListDirTool, ReadFileTool, WriteFileTool
from runtime.tools.python_exec import PythonExecTool
from runtime.tools.terminal import TerminalTool


@pytest.fixture
def config(tmp_path: Path) -> RuntimeConfig:
    cfg = RuntimeConfig()
    cfg.workspace_dir = tmp_path / "ws"
    cfg.audit_log_path = tmp_path / "audit.jsonl"
    cfg.session_db_path = tmp_path / "sessions.db"
    cfg.default_timeout = 10.0
    cfg.ensure_directories()
    return cfg


@pytest.mark.asyncio
async def test_terminal_runs_command(config: RuntimeConfig) -> None:
    out = await TerminalTool(config).execute(command="echo hello-runtime")
    assert "hello-runtime" in out


@pytest.mark.asyncio
async def test_terminal_reports_nonzero_exit(config: RuntimeConfig) -> None:
    out = await TerminalTool(config).execute(command="exit 3")
    assert "exited with code 3" in out


@pytest.mark.asyncio
async def test_terminal_captures_stderr(config: RuntimeConfig) -> None:
    out = await TerminalTool(config).execute(command="echo oops >&2")
    assert "[STDERR]" in out
    assert "oops" in out


@pytest.mark.asyncio
async def test_terminal_timeout_is_bounded(config: RuntimeConfig) -> None:
    out = await TerminalTool(config).execute(command="sleep 5", timeout=1)
    assert "[TIMEOUT]" in out


@pytest.mark.asyncio
async def test_terminal_blocks_destructive_command(config: RuntimeConfig) -> None:
    with pytest.raises(SecurityViolation):
        await TerminalTool(config).execute(command="rm -rf /")


@pytest.mark.asyncio
async def test_terminal_output_is_truncated(config: RuntimeConfig) -> None:
    config.max_output_bytes = 2048
    out = await TerminalTool(config).execute(
        command="python3 -c \"print('A'*200000)\"", timeout=20
    )
    assert "OUTPUT TRUNCATED" in out
    assert len(out.encode()) < 4096


@pytest.mark.asyncio
async def test_terminal_runs_inside_workspace(config: RuntimeConfig) -> None:
    out = await TerminalTool(config).execute(command="pwd")
    assert str(config.workspace_dir) in out


@pytest.mark.asyncio
async def test_terminal_env_has_no_secrets(
    config: RuntimeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak-canary")
    out = await TerminalTool(config).execute(command="env | grep OPENAI_API_KEY || echo NO_LEAK")
    assert "sk-leak-canary" not in out
    assert "NO_LEAK" in out


@pytest.mark.asyncio
async def test_python_exec_runs_code(config: RuntimeConfig) -> None:
    out = await PythonExecTool(config).execute(code="print(6*7)")
    assert "42" in out


@pytest.mark.asyncio
async def test_python_exec_reports_traceback(config: RuntimeConfig) -> None:
    out = await PythonExecTool(config).execute(code="raise ValueError('boom')")
    assert "ValueError" in out
    assert "boom" in out


@pytest.mark.asyncio
async def test_python_exec_timeout(config: RuntimeConfig) -> None:
    out = await PythonExecTool(config).execute(code="import time; time.sleep(5)", timeout=1)
    assert "[TIMEOUT]" in out


@pytest.mark.asyncio
async def test_write_then_read_file(config: RuntimeConfig) -> None:
    write_out = await WriteFileTool(config).execute(path="notes/a.txt", content="isi berkas")
    assert "[OK]" in write_out
    read_out = await ReadFileTool(config).execute(path="notes/a.txt")
    assert read_out == "isi berkas"


@pytest.mark.asyncio
async def test_read_missing_file_is_error_string(config: RuntimeConfig) -> None:
    out = await ReadFileTool(config).execute(path="ghost.txt")
    assert "[ERROR] File not found" in out


@pytest.mark.asyncio
async def test_file_tools_block_traversal(config: RuntimeConfig) -> None:
    with pytest.raises(SecurityViolation):
        await ReadFileTool(config).execute(path="../../etc/passwd")
    with pytest.raises(SecurityViolation):
        await WriteFileTool(config).execute(path="../escape.txt", content="x")


@pytest.mark.asyncio
async def test_list_dir(config: RuntimeConfig) -> None:
    await WriteFileTool(config).execute(path="one.txt", content="1")
    await WriteFileTool(config).execute(path="sub/two.txt", content="2")
    out = await ListDirTool(config).execute()
    assert "[FILE] one.txt" in out
    assert "[DIR] sub" in out


@pytest.mark.asyncio
async def test_list_dir_missing_path(config: RuntimeConfig) -> None:
    out = await ListDirTool(config).execute(path="nope")
    assert "[ERROR] Directory not found" in out
