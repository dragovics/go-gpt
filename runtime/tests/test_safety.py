"""Tests for the safety layer: path confinement, command blocking, env sanitization, truncation."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from runtime.config import RuntimeConfig
from runtime.safety import (
    SecurityViolation,
    check_command_safety,
    check_path_confinement,
    sanitize_env,
    truncate_output,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def test_relative_path_allowed(workspace: Path) -> None:
    resolved = check_path_confinement("notes/todo.md", workspace)
    assert resolved == (workspace / "notes/todo.md").resolve()


def test_traversal_escape_blocked(workspace: Path) -> None:
    with pytest.raises(SecurityViolation):
        check_path_confinement("../../etc/passwd", workspace)


def test_absolute_path_outside_workspace_blocked(workspace: Path) -> None:
    with pytest.raises(SecurityViolation):
        check_path_confinement("/etc/shadow", workspace)


def test_absolute_path_inside_workspace_allowed(workspace: Path) -> None:
    target = workspace / "inside.txt"
    assert check_path_confinement(str(target), workspace) == target.resolve()


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "sudo rm -rf /*",
        ":(){ :|:& };:",
        "mkfs.ext4 /dev/vda1",
        "dd if=/dev/zero of=/dev/vda",
        "shutdown -h now",
    ],
)
def test_destructive_commands_blocked(command: str) -> None:
    config = RuntimeConfig()
    with pytest.raises(SecurityViolation):
        check_command_safety(command, config.blocked_commands)


@pytest.mark.parametrize(
    "command",
    [
        "uptime",
        "ls -la",
        "python3 -c 'print(1)'",
        "grep -r foo . 2> /dev/null",
        "rm -rf ./build",
    ],
)
def test_benign_commands_allowed(command: str) -> None:
    config = RuntimeConfig()
    check_command_safety(command, config.blocked_commands)


def test_raw_device_redirect_blocked() -> None:
    config = RuntimeConfig()
    with pytest.raises(SecurityViolation):
        check_command_safety("echo x > /dev/sda", config.blocked_commands)


def test_sanitize_env_strips_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("VAULT_TOKEN", "hvs.should-not-leak")
    monkeypatch.setenv("MY_PRIVATE_KEY", "should-not-leak")
    monkeypatch.setenv("SAFE_VALUE", "keep-me")

    config = RuntimeConfig()
    env = sanitize_env(config.sensitive_env_keys)

    assert "OPENAI_API_KEY" not in env
    assert "VAULT_TOKEN" not in env
    assert "MY_PRIVATE_KEY" not in env
    assert env["SAFE_VALUE"] == "keep-me"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_truncate_output_under_budget_unchanged() -> None:
    text = "hello world"
    assert truncate_output(text, max_bytes=1024) == text


def test_truncate_output_over_budget_bounded() -> None:
    text = "A" * 100_000
    result = truncate_output(text, max_bytes=4096)
    assert "OUTPUT TRUNCATED" in result
    assert len(result.encode("utf-8")) < 6000
    assert result.startswith("A")
    assert result.endswith("A")


def test_truncate_output_empty() -> None:
    assert truncate_output("", max_bytes=100) == ""
