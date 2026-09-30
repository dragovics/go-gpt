"""Tests for schema validation and the dispatcher's error-as-data contract."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from runtime.config import RuntimeConfig
from runtime.dispatcher import ToolDispatcher
from runtime.safety import SecurityViolation
from runtime.tools.base import BaseTool
from runtime.validator import ValidationError, validate_arguments


class EchoTool(BaseTool):
    name = "echo"
    description = "Echo text back."
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "count": {"type": "integer"},
        },
        "required": ["text"],
    }

    async def execute(self, **kwargs: Any) -> str:
        return str(kwargs.get("text")) * int(kwargs.get("count") or 1)


class BoomTool(BaseTool):
    name = "boom"
    description = "Always raises."
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        raise RuntimeError("internal explosion")


class ForbiddenTool(BaseTool):
    name = "forbidden"
    description = "Always raises a security violation."
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        raise SecurityViolation("not allowed here")


@pytest.fixture
def config(tmp_path: Path) -> RuntimeConfig:
    cfg = RuntimeConfig()
    cfg.workspace_dir = tmp_path / "ws"
    cfg.audit_log_path = tmp_path / "audit.jsonl"
    cfg.session_db_path = tmp_path / "sessions.db"
    cfg.ensure_directories()
    return cfg


@pytest.fixture
def dispatcher(config: RuntimeConfig) -> ToolDispatcher:
    d = ToolDispatcher(config)
    d.register_tool(EchoTool())
    d.register_tool(BoomTool())
    d.register_tool(ForbiddenTool())
    return d


def test_validate_missing_required() -> None:
    with pytest.raises(ValidationError, match="Missing required parameter"):
        validate_arguments(EchoTool.parameters, {})


def test_validate_wrong_type() -> None:
    with pytest.raises(ValidationError, match="expected string"):
        validate_arguments(EchoTool.parameters, {"text": 123})


def test_validate_bool_is_not_integer() -> None:
    with pytest.raises(ValidationError, match="expected integer, got boolean"):
        validate_arguments(EchoTool.parameters, {"text": "a", "count": True})


def test_validate_non_dict_arguments() -> None:
    with pytest.raises(ValidationError, match="must be a JSON object"):
        validate_arguments(EchoTool.parameters, ["not", "a", "dict"])  # type: ignore[arg-type]


def test_validate_happy_path() -> None:
    validate_arguments(EchoTool.parameters, {"text": "hi", "count": 2})


def test_schemas_are_openai_shaped(dispatcher: ToolDispatcher) -> None:
    schemas = dispatcher.get_schemas()
    assert {s["function"]["name"] for s in schemas} == {"echo", "boom", "forbidden"}
    for schema in schemas:
        assert schema["type"] == "function"
        assert set(schema["function"]) == {"name", "description", "parameters"}


@pytest.mark.asyncio
async def test_dispatch_success(dispatcher: ToolDispatcher) -> None:
    out = await dispatcher.dispatch("echo", {"text": "ab", "count": 3})
    assert out == "ababab"


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_returns_error_string(dispatcher: ToolDispatcher) -> None:
    out = await dispatcher.dispatch("nope", {})
    assert "[ERROR] Unknown tool" in out


@pytest.mark.asyncio
async def test_dispatch_validation_error_returns_error_string(dispatcher: ToolDispatcher) -> None:
    out = await dispatcher.dispatch("echo", {})
    assert out.startswith("[SCHEMA VALIDATION ERROR]")


@pytest.mark.asyncio
async def test_dispatch_exception_does_not_propagate(dispatcher: ToolDispatcher) -> None:
    out = await dispatcher.dispatch("boom", {})
    assert out.startswith("[TOOL EXECUTION FAILED]")
    assert "internal explosion" in out


@pytest.mark.asyncio
async def test_dispatch_security_violation_returns_error_string(dispatcher: ToolDispatcher) -> None:
    out = await dispatcher.dispatch("forbidden", {})
    assert out.startswith("[SECURITY VIOLATION]")


@pytest.mark.asyncio
async def test_audit_log_written_and_redacted(
    dispatcher: ToolDispatcher, config: RuntimeConfig
) -> None:
    import json

    await dispatcher.dispatch("echo", {"text": "x"}, session_id="s1")
    dispatcher.register_tool(EchoTool())
    await dispatcher.dispatch("echo", {"text": "y", "api_key": "sk-secret"}, session_id="s1")

    lines = config.audit_log_path.read_text().strip().splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert records[0]["tool_name"] == "echo"
    assert records[0]["success"] is True
    assert records[0]["session_id"] == "s1"
    # second call carried a secret-looking key which must be redacted
    assert records[1]["arguments"]["api_key"] == "***REDACTED***"
    assert "sk-secret" not in lines[1]
