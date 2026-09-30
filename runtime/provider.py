"""OpenAI-compatible LLM provider client with tool-calling support."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import RuntimeConfig

logger = logging.getLogger("runtime.provider")


class ProviderError(RuntimeError):
    """Raised when the LLM provider fails or returns an unusable response."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelResponse:
    content: str | None
    tool_calls: list[ToolCall]
    finish_reason: str | None
    raw: dict[str, Any]


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """Parse the arguments field, which OpenAI sends as a JSON string."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Tolerant retry: escape stray backslashes (common with bash/regex commands)
        import re

        fixed = re.sub(r'\\(?![/"\\bfnrtu]|u[0-9a-fA-F]{4})', r"\\\\", raw)
        try:
            parsed = json.loads(fixed)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Tool arguments are not valid JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise ProviderError("Tool arguments must decode to a JSON object")
    return parsed


class LLMProvider:
    def __init__(self, config: RuntimeConfig):
        self.config = config
        self.base_url = config.llm_base_url.rstrip("/")
        self.api_key = config.llm_api_key
        self.model = config.llm_model

    def _post(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "User-Agent": "go-gpt-runtime/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise ProviderError(f"Provider returned HTTP {exc.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"Provider request failed: {exc}") from None

        # Some relays append SSE terminators even on JSON responses; take the first object.
        raw = raw.strip()
        if not raw:
            raise ProviderError("Provider returned an empty body")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            try:
                obj, _ = decoder.raw_decode(raw)
            except json.JSONDecodeError as exc:
                raise ProviderError(f"Provider returned invalid JSON: {exc}") from None
            if not isinstance(obj, dict):
                raise ProviderError("Provider returned a non-object JSON payload")
            return obj

    def call_model(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 180.0,
    ) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        data = self._post(payload, timeout=timeout)

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            error = data.get("error")
            if error:
                message = error.get("message") if isinstance(error, dict) else str(error)
                raise ProviderError(f"Provider error: {message}")
            raise ProviderError("Provider response contained no choices")

        choice = choices[0]
        if not isinstance(choice, dict):
            raise ProviderError("Provider returned a malformed choice")

        message = choice.get("message")
        if not isinstance(message, dict):
            raise ProviderError("Provider returned a malformed message")

        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ProviderError("Provider returned non-string content")

        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise ProviderError("Provider returned malformed tool_calls")

        tool_calls: list[ToolCall] = []
        for index, item in enumerate(raw_calls):
            if not isinstance(item, dict):
                raise ProviderError("Provider returned a malformed tool call entry")
            function = item.get("function")
            if not isinstance(function, dict):
                raise ProviderError("Tool call is missing its function payload")
            name = function.get("name")
            if not isinstance(name, str) or not name:
                raise ProviderError("Tool call is missing a function name")
            call_id = item.get("id") or f"call_{index}"
            tool_calls.append(
                ToolCall(
                    id=str(call_id),
                    name=name,
                    arguments=_parse_arguments(function.get("arguments")),
                )
            )

        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason"),
            raw=data,
        )
