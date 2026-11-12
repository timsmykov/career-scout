"""Durable admissions: a new preparation advances past previously admitted inputs."""

import json

from career_scout.common import digest, utcnow


def scope(frozen):
    return frozen.get("selection_key") or digest(
        {
            "order": frozen.get("order"),
            "routing": frozen.get("routing"),
            "model": frozen.get("model"),
        }
    )


def route_key(requests, policy):
    return digest({"policy": policy, "keys": [r.evaluation_key for r in requests]})


def initialize(store):
    store.db.execute("""CREATE TABLE IF NOT EXISTS analysis_admissions(
        scope TEXT NOT NULL,route_key TEXT NOT NULL REFERENCES jobs(route_key),
        run_id TEXT NOT NULL REFERENCES analysis_runs(id),admitted_at TEXT NOT NULL,
        PRIMARY KEY(scope,route_key))""")
    # Recover a crash after saving run_jobs but before recording an admission.
    with store.db:
        for row in store.db.execute(
            "SELECT j.route_key,j.run_id,r.config_json,r.created_at FROM run_jobs j "
            "JOIN analysis_runs r ON r.id=j.run_id ORDER BY r.created_at,j.position"
        ).fetchall():
            store.db.execute(
                "INSERT OR IGNORE INTO analysis_admissions VALUES(?,?,?,?)",
                (
                    scope(json.loads(row["config_json"])),
                    row["route_key"],
                    row["run_id"],
                    row["created_at"],
                ),
            )


def admitted(store, frozen, key):
    return (
        store.db.execute(
            "SELECT 1 FROM analysis_admissions WHERE scope=? AND route_key=?", (scope(frozen), key)
        ).fetchone()
        is not None
    )


def record(store, frozen, key, run_id):
    with store.db:
        store.db.execute(
            "INSERT INTO analysis_admissions VALUES(?,?,?,?)",
            (scope(frozen), key, run_id, utcnow()),
        )


def pending_runs(store, frozen):
    return [
        r[0]
        for r in store.db.execute(
            "SELECT DISTINCT a.run_id FROM analysis_admissions a JOIN run_jobs j "
            "ON j.run_id=a.run_id AND j.route_key=a.route_key "
            "WHERE a.scope=? AND (j.status='PENDING' OR ("
            "json_extract(j.outcome_json,'$.selected_profiles') IS NOT NULL AND EXISTS("
            "SELECT 1 FROM json_each(json_extract(j.outcome_json,'$.outcomes')) "
            "WHERE json_extract(value,'$.verdict')='ERROR')))",
            (scope(frozen),),
        )
    ]
