"""
audit_trail.py — Tamper-evident hash-chained audit log.

Every action taken by the parliament is recorded as an immutable entry.
Each entry contains the SHA-256 hash of the previous entry, forming a chain.
If any entry is modified, verify_chain() detects it immediately.

Backed by SQLite with WAL mode — suitable for single-node production,
upgradeable to PostgreSQL by swapping _connect() and the schema.

This is the accountability layer governments require: not just logs,
but cryptographically provable logs.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class AuditEntry:
    entry_id: str
    timestamp: str
    session_id: str
    action: str         # pre_check | deliberate | verify | block | allow | red_team
    actor: str          # SecurityMonitor | Implementer | Critic | SecurityAuditor | Architect | RedTeam | VerificationSpecialist
    outcome: str        # PASS | FAIL | BLOCK | ALLOW | VETO | CLEAR | VULNERABLE | CRITICAL
    task_hash: str      # SHA-256 of the raw task string
    details: str        # JSON-encoded structured details
    prev_hash: str      # SHA-256 of the previous entry (chain link)
    entry_hash: str     # SHA-256 of this entry's content


class AuditTrail:
    """
    Tamper-evident audit log with cryptographic hash chaining.

    Usage:
        trail = AuditTrail("~/.openclaw/parliament/audit.db")
        trail.record(session_id, action, actor, outcome, task, details)
        valid, err = trail.verify_chain()
        trail.export_chain("audit-export.json")
    """

    GENESIS = "genesis"

    def __init__(self, db_path: str):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    entry_id   TEXT PRIMARY KEY,
                    timestamp  TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    action     TEXT NOT NULL,
                    actor      TEXT NOT NULL,
                    outcome    TEXT NOT NULL,
                    task_hash  TEXT NOT NULL,
                    details    TEXT NOT NULL,
                    prev_hash  TEXT NOT NULL,
                    entry_hash TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session   ON audit_log(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON audit_log(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_outcome   ON audit_log(outcome)")
            conn.commit()

    def record(
        self,
        session_id: str,
        action: str,
        actor: str,
        outcome: str,
        task: str,
        details: dict,
    ) -> AuditEntry:
        """Record an action. Returns the committed AuditEntry."""
        entry_id  = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat() + "Z"
        task_hash = hashlib.sha256(task.encode("utf-8")).hexdigest()
        details_json = json.dumps(details, default=str, sort_keys=True)
        prev_hash = self._last_hash()

        content = "|".join([
            entry_id, timestamp, session_id, action, actor,
            outcome, task_hash, details_json, prev_hash,
        ])
        entry_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        entry = AuditEntry(
            entry_id=entry_id,
            timestamp=timestamp,
            session_id=session_id,
            action=action,
            actor=actor,
            outcome=outcome,
            task_hash=task_hash,
            details=details_json,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )

        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_log VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    entry.entry_id, entry.timestamp, entry.session_id,
                    entry.action, entry.actor, entry.outcome, entry.task_hash,
                    entry.details, entry.prev_hash, entry.entry_hash,
                ),
            )
            conn.commit()

        return entry

    def verify_chain(self) -> tuple[bool, Optional[str]]:
        """
        Walk every entry in insertion order and verify the hash chain.
        Returns (chain_valid, error_message).
        Runs in O(n) — call periodically, not per-request.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT entry_id, timestamp, session_id, action, actor, outcome, "
                "task_hash, details, prev_hash, entry_hash "
                "FROM audit_log ORDER BY timestamp ASC"
            ).fetchall()

        prev_hash = self.GENESIS
        for row in rows:
            (
                entry_id, timestamp, session_id, action, actor, outcome,
                task_hash, details, stored_prev_hash, stored_entry_hash,
            ) = row

            if stored_prev_hash != prev_hash:
                return False, (
                    f"Chain broken at entry {entry_id}: "
                    f"expected prev_hash={prev_hash[:16]}... "
                    f"got {stored_prev_hash[:16]}..."
                )

            content = "|".join([
                entry_id, timestamp, session_id, action, actor,
                outcome, task_hash, details, stored_prev_hash,
            ])
            computed = hashlib.sha256(content.encode("utf-8")).hexdigest()

            if computed != stored_entry_hash:
                return False, f"Entry {entry_id} has been tampered with"

            prev_hash = stored_entry_hash

        return True, None

    def query_session(self, session_id: str) -> list[dict]:
        """Return all entries for a session in chronological order."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE session_id=? ORDER BY timestamp ASC",
                (session_id,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def query_outcome(self, outcome: str, limit: int = 100) -> list[dict]:
        """Return entries matching an outcome (e.g. 'BLOCK', 'VULNERABLE')."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE outcome=? ORDER BY timestamp DESC LIMIT ?",
                (outcome, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def export_chain(self, output_path: str):
        """
        Export the full chain as JSON with verification status.
        Suitable for external auditors or compliance review.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY timestamp ASC"
            ).fetchall()

        entries = [self._row_to_dict(r) for r in rows]
        valid, error = self.verify_chain()

        export = {
            "chain_valid": valid,
            "chain_error": error,
            "entry_count": len(entries),
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "entries": entries,
        }

        Path(output_path).write_text(json.dumps(export, indent=2))
        return valid, len(entries)

    def stats(self) -> dict:
        """Summary statistics over the full audit log."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
            by_outcome = conn.execute(
                "SELECT outcome, COUNT(*) FROM audit_log GROUP BY outcome"
            ).fetchall()
            sessions = conn.execute(
                "SELECT COUNT(DISTINCT session_id) FROM audit_log"
            ).fetchone()[0]

        return {
            "total_entries": total,
            "sessions": sessions,
            "by_outcome": dict(by_outcome),
        }

    def _last_hash(self) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT entry_hash FROM audit_log ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else self.GENESIS

    def _row_to_dict(self, row: tuple) -> dict:
        keys = [
            "entry_id", "timestamp", "session_id", "action", "actor",
            "outcome", "task_hash", "details", "prev_hash", "entry_hash",
        ]
        d = dict(zip(keys, row))
        try:
            d["details"] = json.loads(d["details"])
        except (json.JSONDecodeError, KeyError):
            pass
        return d
