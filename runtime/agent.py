"""The agent execution loop: LLM -> tool_calls -> dispatch -> tool_result -> LLM."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .config import RuntimeConfig
from .dispatcher import ToolDispatcher
from .provider import LLMProvider, ModelResponse, ProviderError
from .state.session import SessionManager

logger = logging.getLogger("runtime.agent")

DEFAULT_SYSTEM_PROMPT = (
    "You are an autonomous execution agent running inside a sandboxed Python runtime. "
    "You have real tools; call them instead of guessing or describing what you would do. "
    "Tool results are facts — never invent output. If a tool fails, read the error and adapt. "
    "When the task is complete, reply with a short final answer and no further tool calls."
)


@dataclass
class AgentResult:
    final_answer: str | None
    turns_used: int
    tool_calls_made: int
    stop_reason: str
    messages: list[dict[str, Any]] = field(default_factory=list)


class Agent:
    def __init__(
        self,
        config: RuntimeConfig,
        provider: LLMProvider,
        dispatcher: ToolDispatcher,
        session_manager: SessionManager | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        repeat_limit: int = 3,
    ):
        self.config = config
        self.provider = provider
        self.dispatcher = dispatcher
        self.session_manager = session_manager
        self.system_prompt = system_prompt
        self.repeat_limit = repeat_limit

    def _persist(self, session_id: str, message: dict[str, Any]) -> None:
        if not self.session_manager:
            return
        self.session_manager.add_message(
            session_id=session_id,
            role=message.get("role", "user"),
            content=message.get("content"),
            tool_calls=message.get("tool_calls"),
            tool_call_id=message.get("tool_call_id"),
            name=message.get("name"),
        )

    def _load_history(self, session_id: str) -> list[dict[str, Any]]:
        if not self.session_manager:
            return []
        return self.session_manager.get_messages(session_id)

    @staticmethod
    def _call_fingerprint(name: str, arguments: dict[str, Any]) -> str:
        payload = json.dumps({"name": name, "args": arguments}, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def run(self, user_input: str, session_id: str = "default") -> AgentResult:
        history = self._load_history(session_id)

        messages: list[dict[str, Any]] = []
        if not any(m.get("role") == "system" for m in history):
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(history)

        user_message = {"role": "user", "content": user_input}
        messages.append(user_message)
        self._persist(session_id, user_message)

        tools = self.dispatcher.get_schemas()
        repeat_counter: dict[str, int] = {}
        tool_calls_made = 0

        for turn in range(1, self.config.max_turns + 1):
            try:
                response: ModelResponse = await asyncio.to_thread(
                    self.provider.call_model, messages, tools
                )
            except ProviderError as exc:
                logger.error("Provider failure on turn %s: %s", turn, exc)
                return AgentResult(
                    final_answer=None,
                    turns_used=turn,
                    tool_calls_made=tool_calls_made,
                    stop_reason=f"provider_error: {exc}",
                    messages=messages,
                )

            if not response.tool_calls:
                final = (response.content or "").strip()
                if response.finish_reason == "refusal" and not final:
                    return AgentResult(
                        final_answer=None,
                        turns_used=turn,
                        tool_calls_made=tool_calls_made,
                        stop_reason="model_refusal",
                        messages=messages,
                    )
                assistant_message = {"role": "assistant", "content": final}
                messages.append(assistant_message)
                self._persist(session_id, assistant_message)
                return AgentResult(
                    final_answer=final,
                    turns_used=turn,
                    tool_calls_made=tool_calls_made,
                    stop_reason="final_answer",
                    messages=messages,
                )

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in response.tool_calls
                ],
            }
            messages.append(assistant_message)
            self._persist(session_id, assistant_message)

            for call in response.tool_calls:
                fingerprint = self._call_fingerprint(call.name, call.arguments)
                repeat_counter[fingerprint] = repeat_counter.get(fingerprint, 0) + 1

                if repeat_counter[fingerprint] > self.repeat_limit:
                    output = (
                        f"[LOOP GUARD] Tool '{call.name}' was called with identical arguments "
                        f"{repeat_counter[fingerprint]} times. Stop repeating it and change approach "
                        "or give your final answer."
                    )
                else:
                    output = await self.dispatcher.dispatch(
                        call.name, call.arguments, session_id=session_id
                    )
                    tool_calls_made += 1

                tool_message = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": output,
                }
                messages.append(tool_message)
                self._persist(session_id, tool_message)

        return AgentResult(
            final_answer=None,
            turns_used=self.config.max_turns,
            tool_calls_made=tool_calls_made,
            stop_reason="max_turns_exhausted",
            messages=messages,
        )
