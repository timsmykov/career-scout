"""Permanent vacancy exclusions, independent of CV, run and selection versions."""

import json

from career_scout.common import utcnow

NEGATIVE = {"ROUTING_REVIEW", "PRESCREENED_OUT", "REJECTED_BY_SELECTED"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS vacancy_exclusions(
 source TEXT NOT NULL,source_id TEXT NOT NULL,
 stage TEXT NOT NULL,reason TEXT NOT NULL,origin_run TEXT,origin_key TEXT,
 proof_json TEXT NOT NULL,excluded_at TEXT NOT NULL,
 PRIMARY KEY(source,source_id));
CREATE TABLE IF NOT EXISTS exclusion_bootstrap(id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS exclusion_displacements(
 run_id TEXT NOT NULL,route_key TEXT NOT NULL,status TEXT NOT NULL,outcome_json TEXT,
 excluded_at TEXT NOT NULL,PRIMARY KEY(run_id,route_key));
CREATE TRIGGER IF NOT EXISTS persist_vacancy_exclusion AFTER UPDATE OF status ON run_jobs
WHEN NEW.status IN ('ROUTING_REVIEW','PRESCREENED_OUT','REJECTED_BY_SELECTED')
 AND OLD.status!=NEW.status
 AND json_extract((SELECT config_json FROM analysis_runs WHERE id=NEW.run_id),
                  '$.evaluation_policy')='applications-1'
BEGIN
 INSERT OR IGNORE INTO vacancy_exclusions
 SELECT json_extract(i.payload,'$.vacancy.source'),json_extract(i.payload,'$.vacancy.source_id'),
 CASE NEW.status WHEN 'ROUTING_REVIEW' THEN 'routing'
 WHEN 'PRESCREENED_OUT' THEN 'prescreen' ELSE 'evaluation' END,
 NEW.status,NEW.run_id,NEW.route_key,COALESCE(NEW.outcome_json,'{}'),
 strftime('%Y-%m-%dT%H:%M:%fZ','now')
 FROM jobs j JOIN inputs i ON i.key=json_extract(j.manifest_json,'$.keys[0]')
 WHERE j.route_key=NEW.route_key;
END;
CREATE TRIGGER IF NOT EXISTS prevent_excluded_admission BEFORE INSERT ON run_jobs
WHEN EXISTS(SELECT 1 FROM vacancy_exclusions e JOIN jobs j
 JOIN inputs i ON i.key=json_extract(j.manifest_json,'$.keys[0]')
 WHERE j.route_key=NEW.route_key
 AND e.source=json_extract(i.payload,'$.vacancy.source')
 AND e.source_id=json_extract(i.payload,'$.vacancy.source_id'))
BEGIN SELECT RAISE(IGNORE); END;
CREATE TRIGGER IF NOT EXISTS prevent_excluded_resurrection BEFORE UPDATE OF status ON run_jobs
WHEN OLD.status='EXCLUDED' AND NEW.status!='EXCLUDED'
 AND EXISTS(SELECT 1 FROM vacancy_exclusions e JOIN jobs j
 JOIN inputs i ON i.key=json_extract(j.manifest_json,'$.keys[0]')
 WHERE j.route_key=NEW.route_key AND e.source=json_extract(i.payload,'$.vacancy.source')
 AND e.source_id=json_extract(i.payload,'$.vacancy.source_id'))
BEGIN SELECT RAISE(IGNORE); END;
"""


def exists(db):
    return bool(
        db.execute("SELECT 1 FROM sqlite_master WHERE name='vacancy_exclusions'").fetchone()
    )


# INSERT writers (e.g. imports) must obey the same invariant as UPDATE writers.
_update_trigger = SCHEMA[
    SCHEMA.index("CREATE TRIGGER IF NOT EXISTS persist_vacancy_exclusion ") : SCHEMA.index(
        "CREATE TRIGGER IF NOT EXISTS prevent_excluded_admission "
    )
]
SCHEMA += _update_trigger.replace(
    "persist_vacancy_exclusion AFTER UPDATE OF status",
    "persist_vacancy_exclusion_insert AFTER INSERT",
).replace(" AND OLD.status!=NEW.status\n", "")


def identity_blocked(db, source, source_id):
    return exists(db) and bool(
        db.execute(
            "SELECT 1 FROM vacancy_exclusions WHERE source=? AND source_id=?", (source, source_id)
        ).fetchone()
    )


def blocked(store, vacancy):
    return identity_blocked(store.db, vacancy.source, vacancy.source_id)


def record(store, vacancy, *, stage, reason, origin_run=None, origin_key=None, proof=None):
    with store.db:
        store.db.execute(
            "INSERT OR IGNORE INTO vacancy_exclusions VALUES(?,?,?,?,?,?,?,?)",
            (
                vacancy.source,
                vacancy.source_id,
                stage,
                reason,
                origin_run,
                origin_key,
                json.dumps(proof or {}, ensure_ascii=False),
                utcnow(),
            ),
        )


def initialize(store):
    if not exists(store.db):
        # Triggers protect live writers that loaded their Python code before this migration.
        store.db.executescript("BEGIN IMMEDIATE;" + SCHEMA + "COMMIT;")
    if not store.db.execute("SELECT 1 FROM exclusion_bootstrap WHERE id=1").fetchone():
        latest = {}
        for row in store.db.execute(
            "SELECT j.*,r.config_json,json_extract(i.payload,'$.vacancy.source') AS source,"
            "json_extract(i.payload,'$.vacancy.source_id') AS source_id "
            "FROM run_jobs j JOIN analysis_runs r ON r.id=j.run_id "
            "JOIN jobs m ON m.route_key=j.route_key "
            "JOIN inputs i ON i.key=json_extract(m.manifest_json,'$.keys[0]') "
            "WHERE j.status IN ('ACCEPTED','ROUTING_REVIEW',"
            "'PRESCREENED_OUT','REJECTED_BY_SELECTED') "
            "AND json_extract(r.config_json,'$.evaluation_policy')='applications-1' "
            "ORDER BY r.created_at,r.rowid,j.position"
        ).fetchall():
            latest[(row["source"], row["source_id"])] = row
        for row in latest.values():
            if row["status"] in NEGATIVE:
                record_identity(
                    store,
                    row["source"],
                    row["source_id"],
                    stage={
                        "ROUTING_REVIEW": "routing",
                        "PRESCREENED_OUT": "prescreen",
                        "REJECTED_BY_SELECTED": "evaluation",
                    }[row["status"]],
                    reason=row["status"],
                    origin_run=row["run_id"],
                    origin_key=row["route_key"],
                    proof=json.loads(row["outcome_json"] or "{}"),
                )
        with store.db:
            store.db.execute("INSERT OR IGNORE INTO exclusion_bootstrap VALUES(1)")
    if (
        store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='career_assistant_events'"
        ).fetchone()
        and not store.db.execute("SELECT 1 FROM exclusion_bootstrap WHERE id=2").fetchone()
    ):
        owner = {}
        for event in store.db.execute(
            "SELECT * FROM career_assistant_events WHERE kind IN "
            "('dismissed','withdrawn','rejected','reopened') ORDER BY sequence"
        ).fetchall():
            owner[(event["source"], event["source_id"])] = event
        for event in owner.values():
            if event["kind"] != "reopened":
                record_identity(
                    store,
                    event["source"],
                    event["source_id"],
                    stage="owner",
                    reason=event["kind"],
                    proof={"operation_id": event["operation_id"], "evidence": event["evidence"]},
                )
        with store.db:
            store.db.execute("INSERT OR IGNORE INTO exclusion_bootstrap VALUES(2)")


def apply_run(store, run_id):
    for row in store.db.execute(
        "SELECT j.* FROM run_jobs j JOIN jobs m ON m.route_key=j.route_key "
        "JOIN inputs i ON i.key=json_extract(m.manifest_json,'$.keys[0]') "
        "JOIN vacancy_exclusions e ON e.source=json_extract(i.payload,'$.vacancy.source') "
        "AND e.source_id=json_extract(i.payload,'$.vacancy.source_id') "
        "WHERE j.run_id=? AND j.status NOT IN "
        "('EXCLUDED','ROUTING_REVIEW','PRESCREENED_OUT','REJECTED_BY_SELECTED')",
        (run_id,),
    ).fetchall():
        with store.db:
            store.db.execute(
                "INSERT OR IGNORE INTO exclusion_displacements VALUES(?,?,?,?,?)",
                (run_id, row["route_key"], row["status"], row["outcome_json"], utcnow()),
            )
            store.db.execute(
                "UPDATE run_jobs SET status='EXCLUDED',outcome_json=? "
                "WHERE run_id=? AND route_key=?",
                (
                    json.dumps({"status": "EXCLUDED", "reason": "permanent_exclusion"}),
                    run_id,
                    row["route_key"],
                ),
            )


def record_identity(
    store, source, source_id, *, stage, reason, proof=None, origin_run=None, origin_key=None
):
    with store.db:
        store.db.execute(
            "INSERT OR IGNORE INTO vacancy_exclusions VALUES(?,?,?,?,?,?,?,?)",
            (
                source,
                source_id,
                stage,
                reason,
                origin_run,
                origin_key,
                json.dumps(proof or {}, ensure_ascii=False),
                utcnow(),
            ),
        )


def sync_collector(store, path, policies):
    """Bootstrap current policies, then latch every future deterministic rejection."""
    import sqlite3

    store.db.execute(
        "CREATE TABLE IF NOT EXISTS exclusion_filter_cursors("
        "path TEXT PRIMARY KEY,position INTEGER NOT NULL)"
    )
    name = str(path.resolve())
    cursor = store.db.execute(
        "SELECT position FROM exclusion_filter_cursors WHERE path=?", (name,)
    ).fetchone()
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        db.execute("BEGIN")
        watermark = db.execute("SELECT COALESCE(MAX(rowid),0) FROM filter_decisions").fetchone()[0]
        rows = []
        if cursor:
            rows = db.execute(
                "SELECT source,source_id,payload FROM filter_decisions "
                "WHERE rowid>? AND rowid<=? AND json_extract(payload,'$.status')='FILTERED_OUT'",
                (cursor[0], watermark),
            ).fetchall()
        else:
            for source, policy in policies.items():
                rows.extend(
                    db.execute(
                        "SELECT f.source,f.source_id,f.payload FROM filter_decisions f "
                        "JOIN vacancies v ON v.source=f.source AND v.source_id=f.source_id "
                        "AND v.content_hash=f.content_hash WHERE f.source=? AND f.policy_hash=? "
                        "AND f.rowid<=? AND json_extract(f.payload,'$.status')='FILTERED_OUT'",
                        (source, policy["policy_hash"], watermark),
                    ).fetchall()
                )
        columns = {r[1] for r in db.execute("PRAGMA table_info(vacancies)")}
        if "detail_status" in columns:
            rows.extend(
                db.execute(
                    'SELECT source,source_id,\'{"status":"ARCHIVED"}\' FROM vacancies '
                    "WHERE detail_status='archived'"
                ).fetchall()
            )
        with store.db:
            store.db.executemany(
                "INSERT OR IGNORE INTO vacancy_exclusions VALUES(?,?,?,?,?,?,?,?)",
                [
                    (source, ident, "collector_filter", "FILTERED_OUT", None, None, proof, utcnow())
                    for source, ident, proof in rows
                ],
            )
            store.db.execute(
                "INSERT INTO exclusion_filter_cursors VALUES(?,?) "
                "ON CONFLICT(path) DO UPDATE SET position=excluded.position",
                (name, watermark),
            )
        return len(rows)
    finally:
        db.close()


def restore_owner(store, source, source_id, *, operation_id, evidence):
    """Existing explicit owner reopen can undo only their own dismissal, never model rejection."""
    row = store.db.execute(
        "SELECT * FROM vacancy_exclusions WHERE source=? AND source_id=?", (source, source_id)
    ).fetchone()
    if row is None or row["stage"] != "owner":
        return
    with store.db:
        store.db.execute(
            "CREATE TABLE IF NOT EXISTS exclusion_releases(source TEXT,source_id TEXT,"
            "operation_id TEXT,proof_json TEXT,released_at TEXT)"
        )
        store.db.execute(
            "INSERT INTO exclusion_releases VALUES(?,?,?,?,?)",
            (
                source,
                source_id,
                operation_id,
                json.dumps({"old": dict(row), "evidence": evidence}),
                utcnow(),
            ),
        )
        store.db.execute(
            "DELETE FROM vacancy_exclusions WHERE source=? AND source_id=?", (source, source_id)
        )
        for old in store.db.execute("SELECT * FROM exclusion_displacements").fetchall():
            v = store.inputs(old["route_key"])[0].vacancy
            if (v.source, v.source_id) == (source, source_id):
                store.db.execute(
                    "UPDATE run_jobs SET status=?,outcome_json=? "
                    "WHERE run_id=? AND route_key=? AND status='EXCLUDED'",
                    (old["status"], old["outcome_json"], old["run_id"], old["route_key"]),
                )
