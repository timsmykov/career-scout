"""Atomic, backed-up schema upgrade; immutable source snapshots remain byte-for-byte."""

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from career_scout.common import SourceError
from career_scout.normalization import NORMALIZER, project_snapshot

DDL = """
ALTER TABLE runs ADD COLUMN source TEXT NOT NULL DEFAULT 'hh'
 CHECK(source IN ('hh','habr','superjob','trudvsem'));
CREATE UNIQUE INDEX runs_identity ON runs(id,source);
CREATE UNIQUE INDEX query_run_identity ON queries(id,run_id);
ALTER TABLE vacancies RENAME TO legacy_vacancies;
ALTER TABLE snapshots RENAME TO legacy_snapshots;
ALTER TABLE discoveries RENAME TO legacy_discoveries;
DROP INDEX discoveries_run;
CREATE TABLE vacancies (
 source TEXT NOT NULL CHECK(source IN ('hh','habr','superjob','trudvsem')),
 source_id TEXT NOT NULL CHECK(length(source_id)>0), listing_json TEXT NOT NULL,
 listing_hash TEXT NOT NULL, detail_status TEXT NOT NULL,
 content_hash TEXT, detail_fetched_at TEXT, PRIMARY KEY(source,source_id)
);
CREATE TABLE snapshots (
 source TEXT NOT NULL, source_id TEXT NOT NULL, content_hash TEXT NOT NULL,
 fetched_at TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(source,source_id,content_hash),
 FOREIGN KEY(source,source_id) REFERENCES vacancies(source,source_id)
);
CREATE TABLE normalized_snapshots (
 source TEXT NOT NULL, source_id TEXT NOT NULL, content_hash TEXT NOT NULL,
 normalizer_version TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(source,source_id,content_hash,normalizer_version),
 FOREIGN KEY(source,source_id,content_hash) REFERENCES snapshots(source,source_id,content_hash)
);
CREATE TABLE discoveries (
 run_id TEXT NOT NULL, query_id INTEGER NOT NULL, source TEXT NOT NULL, source_id TEXT NOT NULL,
 PRIMARY KEY(run_id,query_id,source,source_id),
 FOREIGN KEY(run_id,source) REFERENCES runs(id,source),
 FOREIGN KEY(query_id,run_id) REFERENCES queries(id,run_id),
 FOREIGN KEY(source,source_id) REFERENCES vacancies(source,source_id)
);
CREATE INDEX discoveries_run ON discoveries(run_id,source,source_id);
INSERT INTO vacancies SELECT 'hh',id,listing_json,listing_hash,detail_status,
 content_hash,detail_fetched_at FROM legacy_vacancies;
INSERT INTO snapshots SELECT 'hh',vacancy_id,content_hash,fetched_at,payload FROM legacy_snapshots;
INSERT INTO discoveries SELECT run_id,query_id,'hh',vacancy_id FROM legacy_discoveries;
"""


def backup_schema(db: sqlite3.Connection, path: Path, version: int) -> Path:
    folder = path.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = folder / f"{path.name}.v{version}-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    with sqlite3.connect(target) as copy:
        db.backup(copy)
        if copy.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise SourceError("migration_backup_failed")
    return target


def backup_v1(db: sqlite3.Connection, path: Path) -> Path:
    return backup_schema(db, path, 1)


def upgrade_v1(db: sqlite3.Connection, *, commit: bool = True):
    """Caller holds the process lock; new databases may already have a transaction."""
    try:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        for statement in DDL.split(";"):
            if statement.strip():
                db.execute(statement)
        for row in db.execute("SELECT source,source_id,content_hash,payload FROM snapshots"):
            snapshot = project_snapshot(json.loads(row["payload"]))
            if (snapshot["source"], snapshot["source_id"], snapshot["content_hash"]) != (
                row["source"],
                row["source_id"],
                row["content_hash"],
            ):
                raise SourceError("migration_snapshot_identity_mismatch")
            db.execute(
                "INSERT INTO normalized_snapshots VALUES(?,?,?,?,?)",
                (
                    row["source"],
                    row["source_id"],
                    row["content_hash"],
                    NORMALIZER,
                    json.dumps(snapshot, ensure_ascii=False),
                ),
            )
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise SourceError("migration_foreign_key_error")
        for new, old in (
            ("vacancies", "legacy_vacancies"),
            ("snapshots", "legacy_snapshots"),
            ("discoveries", "legacy_discoveries"),
        ):
            if (
                db.execute("SELECT COUNT(*) FROM " + new).fetchone()[0]
                != db.execute("SELECT COUNT(*) FROM " + old).fetchone()[0]
            ):
                raise SourceError("migration_row_count_mismatch")
        if db.execute(
            "SELECT COUNT(*) FROM vacancies v WHERE v.content_hash IS NOT NULL "
            "AND NOT EXISTS(SELECT 1 FROM snapshots s WHERE s.source=v.source "
            "AND s.source_id=v.source_id AND s.content_hash=v.content_hash)"
        ).fetchone()[0]:
            raise SourceError("migration_current_snapshot_missing")
        for table in ("legacy_discoveries", "legacy_snapshots", "legacy_vacancies"):
            db.execute("DROP TABLE " + table)
        db.execute("PRAGMA user_version=2")
        if commit:
            db.commit()
    except (ValidationError, ValueError, KeyError, TypeError):
        db.rollback()
        raise SourceError("migration_normalization_failed") from None
    except BaseException:
        db.rollback()
        raise


FILTER_DDL = """
CREATE TABLE filter_policies (
 policy_hash TEXT PRIMARY KEY, engine_version TEXT NOT NULL,
 policy_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE run_filter_policies (
 run_id TEXT PRIMARY KEY REFERENCES runs(id),
 policy_hash TEXT NOT NULL REFERENCES filter_policies(policy_hash)
);
CREATE TABLE filter_decisions (
 source TEXT NOT NULL, source_id TEXT NOT NULL, content_hash TEXT NOT NULL,
 normalizer_version TEXT NOT NULL,
 policy_hash TEXT NOT NULL REFERENCES filter_policies(policy_hash),
 decision TEXT NOT NULL CHECK(decision IN ('KEEP','KEEP_UNKNOWN','FILTERED_OUT')),
 payload TEXT NOT NULL, evaluated_at TEXT NOT NULL,
 PRIMARY KEY(source,source_id,content_hash,normalizer_version,policy_hash),
 FOREIGN KEY(source,source_id,content_hash,normalizer_version)
 REFERENCES normalized_snapshots(source,source_id,content_hash,normalizer_version)
);
"""


def upgrade_v2(db: sqlite3.Connection, *, commit: bool = True):
    try:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        for statement in FILTER_DDL.split(";"):
            if statement.strip():
                db.execute(statement)
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise SourceError("migration_foreign_key_error")
        db.execute("PRAGMA user_version=3")
        if commit:
            db.commit()
    except BaseException:
        db.rollback()
        raise


def upgrade_v3(db: sqlite3.Connection, *, commit: bool = True):
    try:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        db.execute(
            "CREATE TABLE query_pagination (query_id INTEGER PRIMARY KEY REFERENCES queries(id), "
            "page_size INTEGER NOT NULL CHECK(page_size BETWEEN 1 AND 100))"
        )
        db.execute("PRAGMA user_version=4")
        if commit:
            db.commit()
    except BaseException:
        db.rollback()
        raise


CAMPAIGN_DDL = """
CREATE TABLE campaigns (
 id TEXT PRIMARY KEY, mode TEXT NOT NULL CHECK(mode IN ('initial','new')),
 created_at TEXT NOT NULL, plan_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'partial'
);
CREATE TABLE campaign_runs (
 campaign_id TEXT NOT NULL REFERENCES campaigns(id), source TEXT NOT NULL,
 run_id TEXT NOT NULL UNIQUE REFERENCES runs(id), PRIMARY KEY(campaign_id,source),
 UNIQUE(campaign_id,source,run_id)
);
CREATE TABLE campaign_items (
 campaign_id TEXT NOT NULL, source TEXT NOT NULL, source_id TEXT NOT NULL,
 origin TEXT NOT NULL CHECK(origin IN ('new','backlog','known')),
 PRIMARY KEY(campaign_id,source,source_id),
 FOREIGN KEY(campaign_id,source) REFERENCES campaign_runs(campaign_id,source),
 FOREIGN KEY(source,source_id) REFERENCES vacancies(source,source_id)
);
CREATE INDEX campaign_items_source ON campaign_items(source,source_id,campaign_id);
CREATE TABLE acquisitions (
 source TEXT NOT NULL, source_id TEXT NOT NULL, content_hash TEXT NOT NULL,
 fetched_at TEXT NOT NULL, campaign_id TEXT NOT NULL, run_id TEXT NOT NULL,
 PRIMARY KEY(source,source_id),
 FOREIGN KEY(source,source_id,content_hash) REFERENCES snapshots(source,source_id,content_hash),
 FOREIGN KEY(campaign_id,source,run_id) REFERENCES campaign_runs(campaign_id,source,run_id)
);
CREATE TABLE detail_failures (
 source TEXT NOT NULL, source_id TEXT NOT NULL, attempts INTEGER NOT NULL,
 retry_at REAL NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','failed')),
 error TEXT NOT NULL, PRIMARY KEY(source,source_id),
 FOREIGN KEY(source,source_id) REFERENCES vacancies(source,source_id)
);
"""


def upgrade_v4(db: sqlite3.Connection):
    try:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        for statement in CAMPAIGN_DDL.split(";"):
            if statement.strip():
                db.execute(statement)
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise SourceError("migration_foreign_key_error")
        db.execute("PRAGMA user_version=5")
        db.commit()
    except BaseException:
        db.rollback()
        raise
