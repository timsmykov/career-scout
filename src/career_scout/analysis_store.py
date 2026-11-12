"""Private evaluator ledger, isolated from the collector database."""

import json
import os
import sqlite3
import uuid
from pathlib import Path

from career_scout.application import parse_result
from career_scout.common import digest, utcnow
from career_scout.evaluation import EvaluationError, WorkerInput, validate_result

APPLICATION_ID = 0x43534131
SCHEMA = """
CREATE TABLE inputs(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE evaluations(key TEXT PRIMARY KEY REFERENCES inputs(key),payload TEXT NOT NULL);
CREATE TABLE analysis_runs(
 id TEXT PRIMARY KEY,created_at TEXT NOT NULL,config_json TEXT NOT NULL,
 max_calls INTEGER NOT NULL,calls INTEGER NOT NULL DEFAULT 0,
 retry_generation INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE jobs(
 route_key TEXT PRIMARY KEY,manifest_json TEXT NOT NULL
);
CREATE TABLE run_jobs(
 run_id TEXT NOT NULL REFERENCES analysis_runs(id),
 route_key TEXT NOT NULL REFERENCES jobs(route_key),
 position INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'PENDING',outcome_json TEXT,
 PRIMARY KEY(run_id,route_key)
);
CREATE TABLE attempts(
 id INTEGER PRIMARY KEY,run_id TEXT NOT NULL REFERENCES analysis_runs(id),
 evaluation_key TEXT NOT NULL REFERENCES inputs(key),started_at TEXT NOT NULL,
 finished_at TEXT,error TEXT,usage_json TEXT,response_json TEXT,
 generation INTEGER NOT NULL
);
"""


class AnalysisStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        appid = self.db.execute("PRAGMA application_id").fetchone()[0]
        tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if appid != APPLICATION_ID and (appid or tables):
            self.db.close()
            raise EvaluationError("not_an_analysis_database", fatal=True)
        if appid == 0:
            self.db.executescript(
                f"BEGIN; PRAGMA application_id={APPLICATION_ID}; PRAGMA user_version=1;"
                + SCHEMA
                + "COMMIT;"
            )
        elif self.db.execute("PRAGMA user_version").fetchone()[0] != 1:
            self.db.close()
            raise EvaluationError("unsupported_analysis_schema", fatal=True)
        os.chmod(path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        from career_scout import exclusions

        exclusions.initialize(self)

    def close(self):
        self.db.close()

    def save_input(self, request: WorkerInput):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO inputs VALUES(?,?)",
                (request.evaluation_key, request.model_dump_json()),
            )
        saved = self.request(request.evaluation_key)
        if saved.semantic_content() != request.semantic_content():
            raise EvaluationError("input_key_collision", fatal=True)

    def request(self, key):
        row = self.db.execute("SELECT payload FROM inputs WHERE key=?", (key,)).fetchone()
        if row is None:
            raise EvaluationError("unknown_evaluation_input", fatal=True)
        return WorkerInput.model_validate_json(row[0])

    def cached(self, request):
        row = self.db.execute(
            "SELECT payload FROM evaluations WHERE key=?", (request.evaluation_key,)
        ).fetchone()
        return validate_result(request, parse_result(json.loads(row[0]))) if row else None

    def save_result(self, request, result):
        validate_result(request, result)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO evaluations VALUES(?,?)",
                (request.evaluation_key, result.model_dump_json()),
            )
        return self.cached(request)

    def create_run(self, config, max_calls):
        if type(max_calls) is not int or max_calls < 0:
            raise EvaluationError("invalid_call_budget", fatal=True)
        rid = str(uuid.uuid4())
        with self.db:
            self.db.execute(
                "INSERT INTO analysis_runs(id,created_at,config_json,max_calls) VALUES(?,?,?,?)",
                (rid, utcnow(), json.dumps(config), max_calls),
            )
        return rid

    def run(self, run_id):
        row = self.db.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise EvaluationError("unknown_analysis_run", fatal=True)
        return row

    def add_job(self, run_id, requests, *, policy="first-pass-1"):
        for request in requests:
            self.save_input(request)
        manifest = {"policy": policy, "keys": [r.evaluation_key for r in requests]}
        route_key = digest(manifest)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO jobs VALUES(?,?)", (route_key, json.dumps(manifest))
            )
            position = self.db.execute(
                "SELECT COUNT(*) FROM run_jobs WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            self.db.execute(
                "INSERT OR IGNORE INTO run_jobs(run_id,route_key,position) VALUES(?,?,?)",
                (run_id, route_key, position),
            )
        return route_key

    def inputs(self, route_key):
        row = self.db.execute(
            "SELECT manifest_json FROM jobs WHERE route_key=?", (route_key,)
        ).fetchone()
        if row is None:
            raise EvaluationError("unknown_analysis_job", fatal=True)
        return [self.request(key) for key in json.loads(row[0])["keys"]]

    def reserve(self, run_id, key):
        with self.db:
            changed = self.db.execute(
                "UPDATE analysis_runs SET calls=calls+1 WHERE id=? AND calls<max_calls", (run_id,)
            ).rowcount
            if not changed:
                raise EvaluationError("analysis_call_budget_exhausted", fatal=True)
            generation = self.run(run_id)["retry_generation"]
            return self.db.execute(
                "INSERT INTO attempts(run_id,evaluation_key,started_at,generation) VALUES(?,?,?,?)",
                (run_id, key, utcnow(), generation),
            ).lastrowid

    def attempt_count(self, run_id, key):
        return self.db.execute(
            "SELECT COUNT(*) FROM attempts a JOIN analysis_runs r ON r.id=a.run_id "
            "WHERE a.run_id=? AND a.evaluation_key=? AND a.generation=r.retry_generation",
            (run_id, key),
        ).fetchone()[0]

    def finish_attempt(self, attempt_id, *, error=None, usage=None, response=None):
        with self.db:
            self.db.execute(
                "UPDATE attempts SET finished_at=?,error=?,usage_json=?,response_json=? WHERE id=?",
                (
                    utcnow(),
                    error,
                    json.dumps(usage) if usage is not None else None,
                    json.dumps(response, ensure_ascii=False) if response is not None else None,
                    attempt_id,
                ),
            )

    def save_outcome(self, run_id, route_key, outcome):
        with self.db:
            self.db.execute(
                "UPDATE run_jobs SET status=?,outcome_json=? WHERE run_id=? AND route_key=?",
                (outcome["status"], json.dumps(outcome, ensure_ascii=False), run_id, route_key),
            )

    def retry(self, run_id, additional_calls):
        if type(additional_calls) is not int or additional_calls < 0:
            raise EvaluationError("invalid_call_budget", fatal=True)
        with self.db:
            self.db.execute(
                "UPDATE analysis_runs SET retry_generation=retry_generation+1,"
                "max_calls=max_calls+? WHERE id=?",
                (additional_calls, run_id),
            )
            self.db.execute(
                "UPDATE run_jobs SET status='PENDING' WHERE run_id=? AND status='PENDING'",
                (run_id,),
            )
