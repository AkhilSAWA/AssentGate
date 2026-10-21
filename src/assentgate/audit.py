"""SQLite audit log of every gate decision and its outcome."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from assentgate.schemas import GateResult, ToolCall

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    tool TEXT NOT NULL,
    arguments TEXT NOT NULL,
    decision TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    affected_fields TEXT NOT NULL,
    reasons TEXT NOT NULL,
    executed INTEGER NOT NULL,
    error TEXT
)
"""


class AuditLog:
    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path)
        self.conn.execute(_SCHEMA)

    def record(
        self, call: ToolCall, result: GateResult, executed: bool, error: str | None = None
    ) -> None:
        self.conn.execute(
            "INSERT INTO audit_events (ts, tool, arguments, decision, decided_by,"
            " affected_fields, reasons, executed, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                call.tool,
                json.dumps(call.arguments, default=str),
                result.decision.value,
                result.decided_by,
                json.dumps(result.affected_fields),
                json.dumps(result.reasons),
                int(executed),
                error,
            ),
        )
        self.conn.commit()

    def events(self) -> list[dict]:
        cursor = self.conn.execute("SELECT * FROM audit_events ORDER BY id")
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
