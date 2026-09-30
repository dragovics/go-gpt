"""Schema collection utilities."""
from __future__ import annotations

from typing import Any

from .dispatcher import ToolDispatcher


def build_tools_catalog(dispatcher: ToolDispatcher) -> list[dict[str, Any]]:
    return dispatcher.get_schemas()
