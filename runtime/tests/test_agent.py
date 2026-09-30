"""Tests for the agent loop, provider parsing, and session persistence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from runtime.agent import Agent
from runtime.config import RuntimeConfig
from runtime.dispatcher import ToolDispatcher
from runtime.provider import LLMProvider, ModelResponse, ProviderError, ToolCall, _parse_arguments
from runtime.state.session import SessionManager
from runtime.tools.base import BaseTool


class CounterTool(BaseTool):
    name = "counter"
    description = "Returns a fixed marker and records how often it ran."
    parameters = {
        "type": "object",
        "properties": {"label": {"type": "string"}},
        "required": ["label"],
    }

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, **kwargs: Any) -> str:
        label = str(kwargs.get("label"))
        self.calls.append(label)
        return f"TOOL_RESULT::{label}"


class FakeProvider:
    """Scripted provider returning queued ModelResponses; records what it was sent."""

    def __init__(self, script: list[ModelResponse]):
        self.script = list(script)
        self.seen_messages: list[list[dict[str, Any]]] = []
        self.seen_tools: list[list[dict[str, Any]] | None] = []

    def call_model(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 180.0,
    ) -> ModelResponse:
        self.seen_messages.append(json.loads(json.dumps(messages)))
        self.seen_tools.append(tools)
        if not self.script:
            raise AssertionError("FakeProvider ran out of scripted responses")
        return self.script.pop(0)


class FailingProvider:
    def call_model(self, *args: Any, **kwargs: Any) -> ModelResponse:
        raise ProviderError("upstream 503")


def text_response(content: str) -> ModelResponse:
    return ModelResponse(content=content, tool_calls=[], finish_reason="stop", raw={})


def tool_response(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> ModelResponse:
    return ModelResponse(
        content=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        finish_reason="tool_calls",
        raw={},
    )


def refusal_response() -> ModelResponse:
    return ModelResponse(content="", tool_calls=[], finish_reason="refusal", raw={})


@pytest.fixture
def config(tmp_path: Path) -> RuntimeConfig:
    cfg = RuntimeConfig()
    cfg.workspace_dir = tmp_path / "ws"
    cfg.audit_log_path = tmp_path / "audit.jsonl"
    cfg.session_db_path = tmp_path / "sessions.db"
    cfg.max_turns = 6
    cfg.ensure_directories()
    return cfg


def build_agent(
    config: RuntimeConfig, provider: Any, tool: BaseTool | None = None, **kwargs: Any
) -> tuple[Agent, ToolDispatcher, SessionManager]:
    dispatcher = ToolDispatcher(config)
    if tool is not None:
        dispatcher.register_tool(tool)
    sessions = SessionManager(config.session_db_path)
    agent = Agent(config, provider, dispatcher, session_manager=sessions, **kwargs)
    return agent, dispatcher, sessions


# --- provider argument parsing ---------------------------------------------------


def test_parse_arguments_from_json_string() -> None:
    assert _parse_arguments('{"command": "uptime"}') == {"command": "uptime"}


def test_parse_arguments_accepts_dict() -> None:
    assert _parse_arguments({"a": 1}) == {"a": 1}


def test_parse_arguments_empty_string_is_empty_dict() -> None:
    assert _parse_arguments("") == {}


def test_parse_arguments_tolerates_bad_escapes() -> None:
    raw = '{"command": "grep -E \\"CPU\\(s\\)\\" /proc/cpuinfo"}'
    parsed = _parse_arguments(raw)
    assert "grep -E" in parsed["command"]


def test_parse_arguments_rejects_garbage() -> None:
    with pytest.raises(ProviderError):
        _parse_arguments("this is not json at all {[")


# --- agent loop ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_final_answer_no_tools(config: RuntimeConfig) -> None:
    provider = FakeProvider([text_response("jawaban langsung")])
    agent, _, _ = build_agent(config, provider)

    result = await agent.run("halo", session_id="s1")

    assert result.final_answer == "jawaban langsung"
    assert result.stop_reason == "final_answer"
    assert result.turns_used == 1
    assert result.tool_calls_made == 0


@pytest.mark.asyncio
async def test_tool_then_final_answer_full_loop(config: RuntimeConfig) -> None:
    tool = CounterTool()
    provider = FakeProvider(
        [
            tool_response("counter", {"label": "first"}),
            text_response("selesai"),
        ]
    )
    agent, _, _ = build_agent(config, provider, tool)

    result = await agent.run("pakai tool", session_id="s1")

    assert result.final_answer == "selesai"
    assert result.tool_calls_made == 1
    assert result.turns_used == 2
    assert tool.calls == ["first"]

    # second LLM call must have received the tool result in OpenAI shape
    second_payload = provider.seen_messages[1]
    tool_messages = [m for m in second_payload if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["content"] == "TOOL_RESULT::first"
    assert tool_messages[0]["tool_call_id"] == "call_1"

    assistant_messages = [m for m in second_payload if m["role"] == "assistant"]
    assert assistant_messages[0]["tool_calls"][0]["function"]["name"] == "counter"
    assert json.loads(assistant_messages[0]["tool_calls"][0]["function"]["arguments"]) == {
        "label": "first"
    }


@pytest.mark.asyncio
async def test_tool_schemas_are_sent_to_provider(config: RuntimeConfig) -> None:
    provider = FakeProvider([text_response("ok")])
    agent, _, _ = build_agent(config, provider, CounterTool())

    await agent.run("halo", session_id="s1")

    sent_tools = provider.seen_tools[0]
    assert sent_tools is not None
    assert sent_tools[0]["function"]["name"] == "counter"


@pytest.mark.asyncio
async def test_max_turns_exhausted_stops_loop(config: RuntimeConfig) -> None:
    config.max_turns = 3
    tool = CounterTool()
    # always asks for a tool, never finishes; arguments vary so the loop guard stays out of the way
    provider = FakeProvider([tool_response("counter", {"label": f"x{i}"}) for i in range(3)])
    agent, _, _ = build_agent(config, provider, tool)

    result = await agent.run("loop terus", session_id="s1")

    assert result.final_answer is None
    assert result.stop_reason == "max_turns_exhausted"
    assert result.turns_used == 3
    assert len(tool.calls) == 3


@pytest.mark.asyncio
async def test_repeat_loop_guard_blocks_identical_calls(config: RuntimeConfig) -> None:
    config.max_turns = 6
    tool = CounterTool()
    provider = FakeProvider([tool_response("counter", {"label": "same"}) for _ in range(6)])
    agent, _, _ = build_agent(config, provider, tool, repeat_limit=2)

    result = await agent.run("ulang terus", session_id="s1")

    assert result.stop_reason == "max_turns_exhausted"
    # executed only up to the repeat limit, then guarded
    assert len(tool.calls) == 2
    guard_hits = [
        m for m in result.messages if m["role"] == "tool" and "[LOOP GUARD]" in m["content"]
    ]
    assert guard_hits


@pytest.mark.asyncio
async def test_provider_error_is_returned_not_raised(config: RuntimeConfig) -> None:
    agent, _, _ = build_agent(config, FailingProvider())

    result = await agent.run("halo", session_id="s1")

    assert result.final_answer is None
    assert result.stop_reason.startswith("provider_error")
    assert "upstream 503" in result.stop_reason


@pytest.mark.asyncio
async def test_model_refusal_is_surfaced_explicitly(config: RuntimeConfig) -> None:
    provider = FakeProvider([refusal_response()])
    agent, _, _ = build_agent(config, provider, CounterTool())

    result = await agent.run("lakukan hal terlarang", session_id="s1")

    assert result.final_answer is None
    assert result.stop_reason == "model_refusal"
    assert result.tool_calls_made == 0


@pytest.mark.asyncio
async def test_empty_but_non_refusal_answer_is_kept(config: RuntimeConfig) -> None:
    provider = FakeProvider([text_response("")])
    agent, _, _ = build_agent(config, provider)

    result = await agent.run("halo", session_id="s1")

    assert result.final_answer == ""
    assert result.stop_reason == "final_answer"


@pytest.mark.asyncio
async def test_failing_tool_feeds_error_back_to_model(config: RuntimeConfig) -> None:
    provider = FakeProvider(
        [
            tool_response("counter", {}),  # missing required 'label' -> schema error
            text_response("sudah gue perbaiki"),
        ]
    )
    agent, _, _ = build_agent(config, provider, CounterTool())

    result = await agent.run("panggil salah", session_id="s1")

    assert result.final_answer == "sudah gue perbaiki"
    second_payload = provider.seen_messages[1]
    tool_messages = [m for m in second_payload if m["role"] == "tool"]
    assert tool_messages[0]["content"].startswith("[SCHEMA VALIDATION ERROR]")


# --- session persistence ---------------------------------------------------------


@pytest.mark.asyncio
async def test_session_persists_across_runs(config: RuntimeConfig) -> None:
    provider = FakeProvider([text_response("pertama"), text_response("kedua")])
    agent, _, sessions = build_agent(config, provider)

    await agent.run("pesan satu", session_id="s1")
    await agent.run("pesan dua", session_id="s1")

    stored = sessions.get_messages("s1")
    roles = [m["role"] for m in stored]
    contents = [m.get("content") for m in stored]

    assert roles.count("user") == 2
    assert roles.count("assistant") == 2
    assert "pesan satu" in contents
    assert "pertama" in contents

    # the second provider call must have seen the first exchange as history
    second_payload = provider.seen_messages[1]
    assert any(m.get("content") == "pesan satu" for m in second_payload)
    assert any(m.get("content") == "pertama" for m in second_payload)


@pytest.mark.asyncio
async def test_sessions_are_isolated(config: RuntimeConfig) -> None:
    provider = FakeProvider([text_response("a"), text_response("b")])
    agent, _, sessions = build_agent(config, provider)

    await agent.run("halo sesi A", session_id="A")
    await agent.run("halo sesi B", session_id="B")

    a_contents = [m.get("content") for m in sessions.get_messages("A")]
    b_contents = [m.get("content") for m in sessions.get_messages("B")]

    assert "halo sesi A" in a_contents
    assert "halo sesi A" not in b_contents
    assert "halo sesi B" in b_contents


@pytest.mark.asyncio
async def test_system_prompt_added_once(config: RuntimeConfig) -> None:
    provider = FakeProvider([text_response("x"), text_response("y")])
    agent, _, _ = build_agent(config, provider)

    await agent.run("satu", session_id="s1")
    await agent.run("dua", session_id="s1")

    second_payload = provider.seen_messages[1]
    system_messages = [m for m in second_payload if m["role"] == "system"]
    assert len(system_messages) == 1


def test_clear_session(config: RuntimeConfig) -> None:
    sessions = SessionManager(config.session_db_path)
    sessions.add_message("s1", "user", "halo")
    assert sessions.get_messages("s1")
    sessions.clear_session("s1")
    assert sessions.get_messages("s1") == []


def test_tool_message_roundtrip_shape(config: RuntimeConfig) -> None:
    sessions = SessionManager(config.session_db_path)
    sessions.add_message(
        "s1",
        "assistant",
        None,
        tool_calls=[{"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}],
    )
    sessions.add_message("s1", "tool", "hasil", tool_call_id="c1", name="t")

    msgs = sessions.get_messages("s1")
    assert msgs[0]["tool_calls"][0]["id"] == "c1"
    assert msgs[1]["tool_call_id"] == "c1"
    assert msgs[1]["name"] == "t"
    assert msgs[1]["content"] == "hasil"


def test_provider_uses_configured_model(config: RuntimeConfig) -> None:
    config.llm_model = "cc/claude-opus-5"
    config.llm_base_url = "http://example.invalid/v1/"
    provider = LLMProvider(config)
    assert provider.model == "cc/claude-opus-5"
    assert provider.base_url == "http://example.invalid/v1"
