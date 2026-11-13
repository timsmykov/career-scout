"""Private local control ledger, atomic versions and idempotent command receipts."""

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from career_scout.common import digest, utcnow

APP_ID = 0x4353434C
SCHEMA = """
CREATE TABLE contexts(id TEXT PRIMARY KEY,version INTEGER NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE context_versions(context_id TEXT,version INTEGER,payload TEXT NOT NULL,
 content_hash TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(context_id,version));
CREATE TABLE plans(id TEXT PRIMARY KEY,context_id TEXT NOT NULL,context_version INTEGER NOT NULL,
 payload TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE runs(id TEXT PRIMARY KEY,context_id TEXT NOT NULL,context_version INTEGER NOT NULL,
 plan_id TEXT NOT NULL,campaign_id TEXT NOT NULL,mode TEXT NOT NULL,status TEXT NOT NULL,
 frozen TEXT NOT NULL,limits_json TEXT NOT NULL,collector TEXT NOT NULL,env_path TEXT NOT NULL,
 token_path TEXT NOT NULL,report TEXT,error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE operations(request_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,
 operation TEXT NOT NULL,status TEXT NOT NULL,receipt TEXT,created_at TEXT NOT NULL);
"""


class CommandError(Exception):
    def __init__(self, code, *, retryable=False, outcome="not_started", details=None):
        super().__init__(code)
        self.code, self.retryable, self.outcome, self.details = code, retryable, outcome, details


def identifier(prefix):
    return prefix + "_" + uuid.uuid4().hex


def envelope(result=None, *, operation_id=None, error=None):
    value = {"schema_version": 1, "status": "error" if error else "ok", "result": result}
    if operation_id:
        value["operation_id"] = operation_id
    if error:
        value["error"] = {
            "code": error.code,
            "retryable": error.retryable,
            "outcome": error.outcome,
        }
        if error.details is not None:
            value["error"]["details"] = error.details
    return value


class AgentState:
    def __init__(self, workspace: Path, *, write=False):
        self.root = workspace.resolve()
        self.path = self.root / "control.sqlite3"
        if not self.path.exists() and not write:
            raise CommandError("workspace_not_initialized")
        if write:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not self.path.exists():
                try:
                    fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    os.close(fd)
                except FileExistsError:
                    pass
        self.db = sqlite3.connect(
            str(self.path) if write else self.path.as_uri() + "?mode=ro", uri=not write, timeout=10
        )
        self.db.row_factory = sqlite3.Row
        if write:
            self.db.execute("BEGIN IMMEDIATE")
        app = self.db.execute("PRAGMA application_id").fetchone()[0]
        if app != APP_ID:
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if not write or app or tables:
                self.db.close()
                raise CommandError("not_agent_workspace")
            # Serialize first-time initialization by SQLite, including competing creators.
            if self.db.execute("PRAGMA application_id").fetchone()[0] == 0:
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        self.db.execute(statement)
                self.db.execute(f"PRAGMA application_id={APP_ID}")
                self.db.execute("PRAGMA user_version=1")
        if write:
            self.db.commit()
        if self.db.execute("PRAGMA user_version").fetchone()[0] != 1:
            self.db.close()
            raise CommandError("unsupported_workspace_version")
        if write:
            os.chmod(self.path, 0o600)
            self.db.execute("PRAGMA journal_mode=WAL")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def existing(self, request_id, operation, payload):
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
            raise CommandError("invalid_request_id")
        fingerprint = digest({"operation": operation, "payload": payload})
        row = self.db.execute(
            "SELECT * FROM operations WHERE request_id=?", (request_id,)
        ).fetchone()
        if row:
            if row["fingerprint"] != fingerprint:
                raise CommandError("request_id_conflict")
            if row["status"] != "finished":
                raise CommandError("operation_incomplete", outcome="unknown")
            return json.loads(row["receipt"])
        return None

    def begin(self, request_id, operation, payload):
        self.db.execute(
            "INSERT INTO operations VALUES(?,?,?,'running',NULL,?)",
            (request_id, digest({"operation": operation, "payload": payload}), operation, utcnow()),
        )

    def finish(self, request_id, receipt):
        self.db.execute(
            "UPDATE operations SET status='finished',receipt=? WHERE request_id=?",
            (json.dumps(receipt, ensure_ascii=False), request_id),
        )

    def mutate(self, request_id, operation, payload, action):
        with self.transaction():
            old = self.existing(request_id, operation, payload)
            if old is not None:
                return old
            self.begin(request_id, operation, payload)
            result = action()
            receipt = envelope(result, operation_id=request_id)
            self.finish(request_id, receipt)
            return receipt

    def context(self, context_id, version=None):
        current = self.db.execute("SELECT * FROM contexts WHERE id=?", (context_id,)).fetchone()
        if current is None:
            raise CommandError("unknown_context")
        version = current["version"] if version is None else version
        row = self.db.execute(
            "SELECT * FROM context_versions WHERE context_id=? AND version=?", (context_id, version)
        ).fetchone()
        if row is None:
            raise CommandError("unknown_context_version")
        return {
            "context_id": context_id,
            "version": version,
            "content_hash": row["content_hash"],
            "created_at": row["created_at"],
            "document": json.loads(row["payload"]),
        }

    def plan(self, plan_id):
        row = self.db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if row is None:
            raise CommandError("unknown_plan")
        result = dict(row)
        result["document"] = json.loads(result.pop("payload"))
        return result

    def run(self, run_id):
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise CommandError("unknown_run")
        result = dict(row)
        for key in ("frozen", "report", "limits_json"):
            result[key] = json.loads(result[key]) if result[key] is not None else None
        return result
