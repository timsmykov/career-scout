"""Transactional source ledger. Search cursors and page discoveries commit together."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args

from career_scout.common import SourceError, utcnow
from career_scout.filtering import evaluate
from career_scout.hh import listing_hash
from career_scout.migrations import (
    backup_schema,
    backup_v1,
    upgrade_v1,
    upgrade_v2,
    upgrade_v3,
    upgrade_v4,
)
from career_scout.models import Identity, Source
from career_scout.normalization import NORMALIZER, project_snapshot
from career_scout.search_config import ENGINE_VERSION, FilterPolicy
from career_scout.source_normalization import listing_hash as native_listing_hash

SCHEMA = """
CREATE TABLE runs (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, plan_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'partial', error TEXT, cooldown_until REAL,
 requests INTEGER NOT NULL DEFAULT 0, pages_saved INTEGER NOT NULL DEFAULT 0,
 rejected INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE queries (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
 query TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
 next_page INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
 truncated INTEGER NOT NULL DEFAULT 0, total INTEGER, served INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE vacancies (
 id TEXT PRIMARY KEY, listing_json TEXT NOT NULL, listing_hash TEXT NOT NULL,
 detail_status TEXT NOT NULL, content_hash TEXT, detail_fetched_at TEXT
);
CREATE TABLE snapshots (
 vacancy_id TEXT NOT NULL REFERENCES vacancies(id), content_hash TEXT NOT NULL,
 fetched_at TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY (vacancy_id, content_hash)
);
CREATE TABLE discoveries (
 run_id TEXT NOT NULL REFERENCES runs(id), query_id INTEGER NOT NULL REFERENCES queries(id),
 vacancy_id TEXT NOT NULL REFERENCES vacancies(id),
 PRIMARY KEY (run_id, query_id, vacancy_id)
);
CREATE TABLE events (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
 at TEXT NOT NULL, kind TEXT NOT NULL, code TEXT NOT NULL
);
CREATE INDEX queries_pending ON queries(run_id,status,served,id);
CREATE INDEX discoveries_run ON discoveries(run_id,vacancy_id);
CREATE TABLE source_state (id TEXT PRIMARY KEY, cooldown_until REAL);
INSERT INTO source_state(id) VALUES('hh');
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.path = path
        self.backup_path = None
        try:
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4, 5):
                raise SourceError("unsupported_database_version")
            self.db.execute("PRAGMA journal_mode=WAL")
            if version < 5:
                with self.lock():
                    version = self.db.execute("PRAGMA user_version").fetchone()[0]
                    if version == 0:
                        self.db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
                    elif version == 1:
                        self.backup_path = backup_v1(self.db, self.path)
                    elif version in (2, 3, 4):
                        self.backup_path = backup_schema(self.db, self.path, version)
                    if version < 2:
                        upgrade_v1(self.db, commit=False)
                    if version < 3:
                        upgrade_v2(self.db, commit=False)
                    if version < 4:
                        upgrade_v3(self.db, commit=False)
                    if version < 5:
                        upgrade_v4(self.db)
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    @contextmanager
    def lock(self):
        # Serial CLI execution per ledger; OS releases the lock on process death.
        # A multi-worker lease queue is deliberately not claimed by this HH slice.
        import fcntl

        with self.path.with_suffix(self.path.suffix + ".lock").open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SourceError("collector_already_running") from None
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def create(
        self,
        queries: list[str],
        start: str,
        end: str,
        per_page: int,
        ttl_hours: float,
        *,
        source: Source = "hh",
        plan_snapshot: dict | None = None,
        commit: bool = True,
    ) -> str:
        if source not in get_args(Source):
            raise SourceError("unknown_source")
        run_id = str(uuid.uuid4())
        plan = {
            "source": source,
            "queries": list(dict.fromkeys(queries)),
            "start": start,
            "end": end,
            "per_page": per_page,
            "ttl_hours": ttl_hours,
        }
        if plan_snapshot is not None:
            for key in plan:
                if plan_snapshot.get(key) != plan[key]:
                    raise SourceError("plan_snapshot_mismatch")
            plan = plan_snapshot
            if plan.get("filter_policy"):
                policy = FilterPolicy.model_validate(plan["filter_policy"])
                if (
                    plan.get("filter_policy_hash") != policy.key()
                    or plan.get("filter_engine") != ENGINE_VERSION
                ):
                    raise SourceError("plan_filter_policy_mismatch")
        with self.db if commit else nullcontext():
            self.db.execute(
                "INSERT INTO runs(id,created_at,plan_json,source) VALUES(?,?,?,?)",
                (run_id, utcnow(), json.dumps(plan, ensure_ascii=False), source),
            )
            self.db.executemany(
                "INSERT INTO queries(run_id,query,start,end) VALUES(?,?,?,?)",
                [(run_id, query, start, end) for query in plan["queries"]],
            )
            self.db.execute("INSERT OR IGNORE INTO source_state(id) VALUES(?)", (source,))
            if plan.get("filter_policy"):
                self.activate_policy(run_id, FilterPolicy.model_validate(plan["filter_policy"]))
        return run_id

    def run(self, run_id: str):
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise SourceError("unknown_run")
        return row

    def next_query(self, run_id: str):
        return self.db.execute(
            "SELECT * FROM queries WHERE run_id=? AND status='pending' ORDER BY served,id LIMIT 1",
            (run_id,),
        ).fetchone()

    def split(self, query, total: int, *, page_size: int | None = None) -> bool:
        end = datetime.fromisoformat(query["end"])
        start = datetime.fromisoformat(query["start"]) if query["start"] else None
        if start is not None:
            if (end - start).total_seconds() <= 1:
                return False
            middle = start + timedelta(seconds=int((end - start).total_seconds() // 2))
        else:
            middle = end - timedelta(days=30)
        with self.db:
            self.db.execute(
                "UPDATE queries SET status='split',total=? WHERE id=?", (total, query["id"])
            )
            for a, b in (
                ((start, middle), (middle, end)) if start else ((middle, end), (None, middle))
            ):
                child = self.db.execute(
                    "INSERT INTO queries(run_id,query,start,end) VALUES(?,?,?,?)",
                    (query["run_id"], query["query"], a.isoformat() if a else "", b.isoformat()),
                ).lastrowid
                if page_size is not None:
                    self.db.execute("INSERT INTO query_pagination VALUES(?,?)", (child, page_size))
        return True

    def replan_page_size(self, query, old_size: int, reason: str):
        """Replace one query atomically, keeping its discoveries and original cursor."""
        if not 2 <= old_size <= 100:
            raise SourceError("invalid_page_replan")
        new_size = old_size // 2
        with self.db:
            self.db.execute("UPDATE queries SET status='replanned' WHERE id=?", (query["id"],))
            child = self.db.execute(
                "INSERT INTO queries(run_id,query,start,end) VALUES(?,?,?,?)",
                (query["run_id"], query["query"], query["start"], query["end"]),
            ).lastrowid
            self.db.execute("INSERT INTO query_pagination VALUES(?,?)", (child, new_size))
            self.event(
                query["run_id"],
                "pagination_replan",
                json.dumps(
                    {
                        "parent_query_id": query["id"],
                        "query_id": child,
                        "old_page_size": old_size,
                        "page_size": new_size,
                        "reason": reason,
                    }
                ),
            )
        return child

    def save_page(self, query, page, ttl_hours: float, *, campaign_id: str | None = None):
        source = self.run(query["run_id"])["source"]
        cutoff = (datetime.now(UTC) - timedelta(hours=ttl_hours)).isoformat()
        with self.db:
            pinned = self.db.execute(
                "SELECT page_size FROM query_pagination WHERE query_id=?", (query["id"],)
            ).fetchone()
            if pinned and pinned[0] != page.per_page:
                raise SourceError("pagination_size_changed")
            self.db.execute(
                "INSERT OR IGNORE INTO query_pagination VALUES(?,?)", (query["id"], page.per_page)
            )
            for item in page.items:
                vid = item.get("id")
                try:
                    Identity(source=source, source_id=vid)
                    valid_identity = True
                except ValueError:
                    valid_identity = False
                if not valid_identity or (source == "hh" and not re.fullmatch(r"[0-9]+", vid)):
                    self.db.execute(
                        "UPDATE runs SET rejected=rejected+1 WHERE id=?", (query["run_id"],)
                    )
                    self.event(query["run_id"], "row_rejected", "invalid_source_id")
                    continue
                fingerprint = (
                    listing_hash(item) if source == "hh" else native_listing_hash(source, item)
                )
                old = self.db.execute(
                    "SELECT * FROM vacancies WHERE source=? AND source_id=?", (source, vid)
                ).fetchone()
                state = "missing"
                if old and campaign_id is None:
                    changed = old["listing_hash"] != fingerprint
                    expired = not old["detail_fetched_at"] or old["detail_fetched_at"] <= cutoff
                    state = old["detail_status"]
                    if changed or expired:
                        state = "stale" if old["content_hash"] else "missing"
                if old is None or campaign_id is None:
                    self.db.execute(
                        "INSERT INTO vacancies"
                        "(source,source_id,listing_json,listing_hash,detail_status) "
                        "VALUES(?,?,?,?,?) "
                        "ON CONFLICT(source,source_id) DO UPDATE "
                        "SET listing_json=excluded.listing_json, "
                        "listing_hash=excluded.listing_hash,detail_status=excluded.detail_status",
                        (source, vid, json.dumps(item, ensure_ascii=False), fingerprint, state),
                    )
                if campaign_id is not None:
                    origin = "new" if old is None else "known" if old["content_hash"] else "backlog"
                    self.db.execute(
                        "INSERT OR IGNORE INTO campaign_items VALUES(?,?,?,?)",
                        (campaign_id, source, vid, origin),
                    )
                self.db.execute(
                    "INSERT OR IGNORE INTO discoveries VALUES(?,?,?,?)",
                    (query["run_id"], query["id"], source, vid),
                )
            cap = page.window_cap
            done = (
                not page.has_more
                if page.has_more is not None
                else not page.items
                or page.page + 1 >= page.pages
                or (cap is not None and (page.page + 1) * page.per_page >= cap)
            )
            truncated = page.truncated or (cap is not None and page.found > cap)
            self.db.execute(
                "UPDATE queries SET next_page=?,status=?,truncated=?,total=?,served=served+1 "
                "WHERE id=?",
                (
                    page.page + 1,
                    "complete" if done else "pending",
                    truncated,
                    page.found,
                    query["id"],
                ),
            )
            self.db.execute(
                "UPDATE runs SET pages_saved=pages_saved+1 WHERE id=?", (query["run_id"],)
            )

    def page_size(self, query_id: int, default: int):
        row = self.db.execute(
            "SELECT page_size FROM query_pagination WHERE query_id=?", (query_id,)
        ).fetchone()
        return row[0] if row else default

    def pending_detail(self, run_id: str):
        return self.db.execute(
            "SELECT DISTINCT v.source,v.source_id FROM vacancies v JOIN discoveries d "
            "ON d.source=v.source AND d.source_id=v.source_id "
            "WHERE d.run_id=? AND v.detail_status IN ('missing','stale','partial') "
            "ORDER BY v.source,v.source_id LIMIT 1",
            (run_id,),
        ).fetchone()

    def save_detail(self, snapshot: dict, *, campaign_id=None, run_id=None):
        normalized = project_snapshot(snapshot)
        source = normalized["source"]
        with self.db:
            if campaign_id is not None:
                old = self.db.execute(
                    "SELECT content_hash FROM vacancies WHERE source=? AND source_id=?",
                    (source, snapshot["source_id"]),
                ).fetchone()
                if old and old[0] is not None:
                    return False
            self.db.execute(
                "INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?)",
                (
                    source,
                    snapshot["source_id"],
                    snapshot["content_hash"],
                    snapshot["detail_fetched_at"],
                    json.dumps(snapshot, ensure_ascii=False),
                ),
            )
            self.db.execute(
                "UPDATE vacancies SET content_hash=?,detail_status=?,detail_fetched_at=? "
                "WHERE source=? AND source_id=?",
                (
                    snapshot["content_hash"],
                    snapshot["detail_status"],
                    snapshot["detail_fetched_at"],
                    source,
                    snapshot["source_id"],
                ),
            )

            self.db.execute(
                "INSERT OR IGNORE INTO normalized_snapshots VALUES(?,?,?,?,?)",
                (
                    source,
                    snapshot["source_id"],
                    snapshot["content_hash"],
                    NORMALIZER,
                    json.dumps(normalized, ensure_ascii=False),
                ),
            )
            if campaign_id is not None:
                self.db.execute(
                    "INSERT OR IGNORE INTO acquisitions VALUES(?,?,?,?,?,?)",
                    (
                        source,
                        snapshot["source_id"],
                        snapshot["content_hash"],
                        snapshot["detail_fetched_at"],
                        campaign_id,
                        run_id,
                    ),
                )
                self.db.execute(
                    "DELETE FROM detail_failures WHERE source=? AND source_id=?",
                    (source, snapshot["source_id"]),
                )
        return True

    def unavailable(self, vid: str, source: Source = "hh"):
        with self.db:
            self.db.execute(
                "UPDATE vacancies SET detail_status='unavailable' WHERE source=? AND source_id=?",
                (source, vid),
            )
            self.db.execute(
                "DELETE FROM detail_failures WHERE source=? AND source_id=?", (source, vid)
            )

    def event(self, run_id: str, kind: str, code: str):
        self.db.execute(
            "INSERT INTO events(run_id,at,kind,code) VALUES(?,?,?,?)",
            (run_id, utcnow(), kind, code),
        )

    def begin(self, run_id: str):
        with self.db:
            self.db.execute("UPDATE runs SET status='partial' WHERE id=?", (run_id,))

    def reserve_request(self, run_id: str):
        # Durable before HTTP: a crash may overcount one reservation, never lose an attempt.
        with self.db:
            self.db.execute("UPDATE runs SET requests=requests+1 WHERE id=?", (run_id,))

    def cooldown(self, source: Source = "hh"):
        row = self.db.execute(
            "SELECT cooldown_until FROM source_state WHERE id=?", (source,)
        ).fetchone()
        return row[0] if row else None

    def finish(self, run_id: str, error: SourceError | None = None):
        report = self.report(run_id)
        complete = not (
            error
            or report["pending_queries"]
            or report["deferred_details"]
            or report["truncated_queries"]
            or report["rejected_rows"]
        )
        status = "complete" if complete else "partial"
        if error and error.code in {
            "access_denied",
            "auth_required",
            "missing_application_credentials",
            "rate_limited",
            "source_adapter_unavailable",
            "source_storage_policy_unconfirmed",
        }:
            status = "blocked"
        with self.db:
            self.db.execute(
                "UPDATE runs SET status=?,error=?,cooldown_until=? WHERE id=?",
                (
                    status,
                    error.code if error else None,
                    error.retry_at if error else None,
                    run_id,
                ),
            )
            if error:
                self.event(run_id, "source_error", error.code)
            if error and error.retry_at:
                self.db.execute(
                    "UPDATE source_state SET cooldown_until=? WHERE id=?",
                    (error.retry_at, self.run(run_id)["source"]),
                )
        return self.report(run_id)

    def report(self, run_id: str) -> dict:
        run = self.run(run_id)
        states = dict(
            self.db.execute(
                "SELECT v.detail_status,COUNT(*) FROM vacancies v WHERE EXISTS "
                "(SELECT 1 FROM discoveries d WHERE d.source=v.source AND d.source_id=v.source_id "
                "AND d.run_id=?) GROUP BY v.detail_status",
                (run_id,),
            ).fetchall()
        )
        queries = self.db.execute("SELECT * FROM queries WHERE run_id=?", (run_id,)).fetchall()
        return {
            "run_id": run_id,
            "status": run["status"],
            "error": run["error"],
            "cooldown_until": run["cooldown_until"],
            "requests": run["requests"],
            "pages_saved": run["pages_saved"],
            "distinct_vacancies": sum(states.values()),
            "full_details": states.get("full", 0),
            "deferred_details": sum(states.get(s, 0) for s in ("missing", "stale", "partial")),
            "unavailable_details": states.get("unavailable", 0),
            "archived_details": states.get("archived", 0),
            "pending_queries": sum(q["status"] == "pending" for q in queries),
            "completed_queries": sum(q["status"] == "complete" for q in queries),
            "truncated_queries": sum(q["truncated"] for q in queries),
            "rejected_rows": run["rejected"],
            "plan": json.loads(run["plan_json"]),
            "filtering": self.filter_summary(run_id),
        }

    def inspect(self, vid: str, source: Source = "hh") -> dict:
        Identity(source=source, source_id=vid)
        row = self.db.execute(
            "SELECT * FROM vacancies WHERE source=? AND source_id=?", (source, vid)
        ).fetchone()
        if row is None:
            raise SourceError("unknown_vacancy")
        snapshots = self.db.execute(
            "SELECT payload FROM snapshots WHERE source=? AND source_id=? ORDER BY fetched_at",
            (source, vid),
        ).fetchall()
        discoveries = [
            dict(d)
            for d in self.db.execute(
                "SELECT d.run_id,d.query_id,q.query FROM discoveries d "
                "JOIN queries q ON q.id=d.query_id "
                "WHERE d.source=? AND d.source_id=?",
                (source, vid),
            )
        ]
        return {
            "source": source,
            "source_id": vid,
            "detail_status": row["detail_status"],
            "current_hash": row["content_hash"],
            "detail_fetched_at": row["detail_fetched_at"],
            "snapshots": [json.loads(s[0]) for s in snapshots],
            "normalized_snapshots": [
                json.loads(row[0])
                for row in self.db.execute(
                    "SELECT payload FROM normalized_snapshots WHERE source=? AND source_id=? "
                    "AND normalizer_version=?",
                    (source, vid, NORMALIZER),
                )
            ],
            "discoveries": discoveries,
            "filter_history": [
                json.loads(row[0])
                for row in self.db.execute(
                    "SELECT payload FROM filter_decisions WHERE source=? AND source_id=? "
                    "ORDER BY evaluated_at",
                    (source, vid),
                )
            ],
        }

    def ready(self, run_id: str):
        self.run(run_id)
        rows = self.db.execute(
            "SELECT s.payload,v.detail_fetched_at FROM normalized_snapshots s JOIN vacancies v "
            "ON s.source=v.source AND s.source_id=v.source_id AND s.content_hash=v.content_hash "
            "WHERE s.normalizer_version=? AND v.detail_status='full' AND EXISTS "
            "(SELECT 1 FROM discoveries d WHERE d.source=v.source AND d.source_id=v.source_id "
            "AND d.run_id=?) ORDER BY v.source,v.source_id",
            (NORMALIZER, run_id),
        ).fetchall()
        return [dict(json.loads(row[0]), detail_fetched_at=row[1]) for row in rows]

    def export(self, run_id: str, *, include_excluded: bool = False):
        # One consistent SQLite read snapshot, even while a collector writes in WAL mode.
        self.db.execute("BEGIN")
        try:
            records = self.ready(run_id)
            summary = self.filter_summary(run_id)
            decisions = self.current_decisions(run_id)
            if summary["applied"] and not include_excluded:
                allowed = {
                    (d["source"], d["source_id"], d["content_hash"])
                    for d in decisions
                    if d["result"]["status"] in ("KEEP", "KEEP_UNKNOWN")
                }
                records = [
                    v
                    for v in records
                    if (v["source"], v["source_id"], v["content_hash"]) in allowed
                ]
            return {
                "schema_version": 2,
                "normalizer_version": NORMALIZER,
                "report": self.report(run_id),
                "selection": {"scope": "all" if include_excluded else "selected", **summary},
                "filter_decisions": decisions,
                "vacancies": records,
            }
        finally:
            self.db.rollback()

    def activate_policy(self, run_id: str, policy: FilterPolicy):
        self.run(run_id)
        self.db.execute(
            "INSERT OR IGNORE INTO filter_policies VALUES(?,?,?,?)",
            (
                policy.key(),
                ENGINE_VERSION,
                json.dumps(policy.model_dump(mode="json"), ensure_ascii=False),
                utcnow(),
            ),
        )
        self.db.execute(
            "INSERT INTO run_filter_policies VALUES(?,?) ON CONFLICT(run_id) "
            "DO UPDATE SET policy_hash=excluded.policy_hash",
            (run_id, policy.key()),
        )

    def active_policy(self, run_id: str):
        return self.db.execute(
            "SELECT p.* FROM filter_policies p JOIN run_filter_policies r "
            "ON p.policy_hash=r.policy_hash WHERE r.run_id=?",
            (run_id,),
        ).fetchone()

    def filter_run(self, run_id: str, policy: FilterPolicy | None = None):
        # Caller owns the collector lock. Evaluate all full current versions; repeat is idempotent.
        with self.db:
            if policy is not None:
                self.activate_policy(run_id, policy)
            saved = self.active_policy(run_id)
            if saved is None:
                return
            if saved["engine_version"] != ENGINE_VERSION:
                raise SourceError("filter_engine_version_unavailable")
            policy = FilterPolicy.model_validate_json(saved["policy_json"])
            for vacancy in self.ready(run_id):
                key = (
                    vacancy["source"],
                    vacancy["source_id"],
                    vacancy["content_hash"],
                    NORMALIZER,
                    policy.key(),
                )
                if self.db.execute(
                    "SELECT 1 FROM filter_decisions WHERE source=? AND source_id=? "
                    "AND content_hash=? AND normalizer_version=? AND policy_hash=?",
                    key,
                ).fetchone():
                    continue
                result = evaluate(vacancy, policy)
                self.db.execute(
                    "INSERT INTO filter_decisions VALUES(?,?,?,?,?,?,?,?)",
                    (
                        *key,
                        result.status,
                        json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
                        utcnow(),
                    ),
                )

    def current_decisions(self, run_id: str):
        rows = self.db.execute(
            "SELECT f.source,f.source_id,f.content_hash,f.payload "
            "FROM filter_decisions f "
            "JOIN run_filter_policies r ON r.policy_hash=f.policy_hash AND r.run_id=? "
            "JOIN vacancies v ON v.source=f.source AND v.source_id=f.source_id "
            "AND v.content_hash=f.content_hash WHERE f.normalizer_version=? "
            "AND v.detail_status='full' "
            "AND EXISTS(SELECT 1 FROM discoveries d WHERE d.run_id=r.run_id "
            "AND d.source=f.source AND d.source_id=f.source_id) ORDER BY f.source,f.source_id",
            (run_id, NORMALIZER),
        ).fetchall()
        return [
            {
                "source": row["source"],
                "source_id": row["source_id"],
                "content_hash": row["content_hash"],
                "result": json.loads(row["payload"]),
            }
            for row in rows
        ]

    def filter_summary(self, run_id: str):
        policy = self.active_policy(run_id)
        if policy is None:
            return {"applied": False, "policy_hash": None}
        decisions = self.current_decisions(run_id)
        counts = {
            status: sum(d["result"]["status"] == status for d in decisions)
            for status in ("KEEP", "KEEP_UNKNOWN", "FILTERED_OUT")
        }
        full = self.db.execute(
            "SELECT COUNT(*) FROM vacancies v WHERE detail_status='full' "
            "AND EXISTS(SELECT 1 FROM discoveries d WHERE d.run_id=? AND d.source=v.source "
            "AND d.source_id=v.source_id)",
            (run_id,),
        ).fetchone()[0]
        return {
            "applied": True,
            "policy_hash": policy["policy_hash"],
            "engine_version": policy["engine_version"],
            **counts,
            "pending": full - len(decisions),
        }
