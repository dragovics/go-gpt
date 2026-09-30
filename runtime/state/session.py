"""SQLite-backed conversation and session state manager."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class SessionManager:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_calls TEXT,
                    tool_call_id TEXT,
                    name TEXT,
                    created_at REAL NOT NULL
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_id ON messages (session_id);"
            )
            conn.commit()

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
        name: str | None = None,
    ) -> None:
        raw_tools = json.dumps(tool_calls) if tool_calls else None
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO messages (session_id, role, content, tool_calls, tool_call_id, name, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, role, content, raw_tools, tool_call_id, name, time.time()),
            )
            conn.commit()

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT role, content, tool_calls, tool_call_id, name
                FROM messages
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()

        result = []
        for r in rows:
            msg: dict[str, Any] = {"role": r["role"]}
            if r["content"] is not None:
                msg["content"] = r["content"]
            if r["tool_calls"]:
                try:
                    msg["tool_calls"] = json.loads(r["tool_calls"])
                except Exception:
                    pass
            if r["tool_call_id"]:
                msg["tool_call_id"] = r["tool_call_id"]
            if r["name"]:
                msg["name"] = r["name"]
            result.append(msg)
        return result

    def clear_session(self, session_id: str) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.commit()
