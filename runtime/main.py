"""CLI entrypoint for the go-gpt tool-calling runtime."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .agent import Agent
from .config import RuntimeConfig
from .dispatcher import ToolDispatcher
from .provider import LLMProvider
from .state.audit import AuditLogger
from .state.session import SessionManager
from .tools.files import ListDirTool, ReadFileTool, WriteFileTool
from .tools.python_exec import PythonExecTool
from .tools.terminal import TerminalTool


def build_runtime(config: RuntimeConfig) -> tuple[Agent, ToolDispatcher]:
    config.ensure_directories()

    audit = AuditLogger(config.audit_log_path)
    dispatcher = ToolDispatcher(config, audit_logger=audit)

    for tool in (
        TerminalTool(config),
        PythonExecTool(config),
        ReadFileTool(config),
        WriteFileTool(config),
        ListDirTool(config),
    ):
        dispatcher.register_tool(tool)

    provider = LLMProvider(config)
    sessions = SessionManager(config.session_db_path)
    agent = Agent(config, provider, dispatcher, session_manager=sessions)
    return agent, dispatcher


async def run_once(prompt: str, session_id: str, config: RuntimeConfig) -> int:
    agent, dispatcher = build_runtime(config)

    print(f"[runtime] model={config.llm_model} endpoint={config.llm_base_url}")
    print(f"[runtime] workspace={config.workspace_dir}")
    print(f"[runtime] tools={[s['function']['name'] for s in dispatcher.get_schemas()]}")
    print(f"[runtime] max_turns={config.max_turns} session={session_id}")
    print("-" * 60)

    result = await agent.run(prompt, session_id=session_id)

    print("-" * 60)
    print(f"[runtime] stop_reason={result.stop_reason}")
    print(f"[runtime] turns_used={result.turns_used} tool_calls={result.tool_calls_made}")
    print("-" * 60)
    if result.final_answer is not None:
        print(result.final_answer or "[model returned an empty final answer]")
        return 0
    print(f"[runtime] no final answer produced ({result.stop_reason})", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="go-gpt-runtime",
        description="Production-grade tool-calling agent runtime (OpenAI-compatible).",
    )
    parser.add_argument("prompt", nargs="+", help="The task for the agent to perform.")
    parser.add_argument("--session", default="default", help="Session id for conversation persistence.")
    parser.add_argument("--model", default=None, help="Override LLM_MODEL.")
    parser.add_argument("--base-url", default=None, help="Override LLM_BASE_URL.")
    parser.add_argument("--workspace", default=None, help="Override WORKSPACE_DIR.")
    parser.add_argument("--max-turns", type=int, default=None, help="Override MAX_TURNS.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    config = RuntimeConfig()
    if args.model:
        config.llm_model = args.model
    if args.base_url:
        config.llm_base_url = args.base_url
    if args.workspace:
        from pathlib import Path

        config.workspace_dir = Path(args.workspace).resolve()
    if args.max_turns:
        config.max_turns = args.max_turns

    prompt = " ".join(args.prompt)
    return asyncio.run(run_once(prompt, args.session, config))


if __name__ == "__main__":
    raise SystemExit(main())
