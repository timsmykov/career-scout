"""A terminal rejection cannot return through a new scope, retry or assistant search."""

import json
import sqlite3

import pytest
from test_analysis import setup as base_setup
from test_applications import setup_config
from test_routed_analysis import prepare_routed

from career_scout import analysis, exclusions
from career_scout.analysis_store import AnalysisStore
from career_scout.routed_pipeline import aggregate, route_jobs


@pytest.fixture
def fixture(tmp_path):
    return base_setup.__wrapped__(tmp_path)


@pytest.mark.parametrize("status", ["ROUTING_REVIEW", "PRESCREENED_OUT", "REJECTED_BY_SELECTED"])
def test_terminal_latch_survives_scope_resume_and_changed_description(fixture, monkeypatch, status):
    file, cfg, values = setup_config(fixture, monkeypatch)
    first = analysis.prepare(file, limit=1, max_calls=0)
    store = AnalysisStore(fixture[0] / "analysis.sqlite3")
    key = store.db.execute(
        "SELECT route_key FROM run_jobs WHERE run_id=?", (first["run_id"],)
    ).fetchone()[0]
    store.save_outcome(first["run_id"], key, {"status": status})
    assert exclusions.identity_blocked(store.db, "hh", values[0]["source_id"])
    store.close()
    cfg["owner_attributes"] = {"changed": True}
    file.write_text(json.dumps(cfg))
    values[0]["description"] = "Completely new description with same source identity"
    prepared = analysis.prepare(file, limit=5, max_calls=0, reprocess=True)
    assert prepared["permanently_excluded_skipped"] == 1
    assert prepared["jobs"] == 4
    store = AnalysisStore(fixture[0] / "analysis.sqlite3")
    assert store.db.execute("SELECT COUNT(*) FROM vacancy_exclusions").fetchone()[0] == 1
    store.close()


def test_one_profile_reject_and_error_do_not_exclude_vacancy(fixture):
    store, rid, frozen, jobs = prepare_routed(fixture, [["product", "automation"]])
    config = json.loads(store.run(rid)["config_json"])
    config["evaluation_policy"] = "applications-1"
    with store.db:
        store.db.execute(
            "UPDATE analysis_runs SET config_json=? WHERE id=?", (json.dumps(config), rid)
        )
    # A technical failure/pending is not a terminal candidate rejection.
    store.save_outcome(
        rid,
        jobs[0][0],
        {
            "status": "PENDING",
            "outcomes": {"product": {"verdict": "REJECT"}, "automation": {"verdict": "ERROR"}},
        },
    )
    assert store.db.execute("SELECT COUNT(*) FROM vacancy_exclusions").fetchone()[0] == 0
    store.save_outcome(
        rid,
        jobs[0][0],
        {
            "status": "ACCEPTED",
            "outcomes": {"product": {"verdict": "REJECT"}, "automation": {"verdict": "PASS"}},
        },
    )
    assert store.db.execute("SELECT COUNT(*) FROM vacancy_exclusions").fetchone()[0] == 0
    store.close()


def test_prepared_pending_is_excluded_before_native_call_and_counts_complete(fixture):
    store, rid, frozen, jobs = prepare_routed(fixture, [["product"]])
    with store.db:
        store.db.execute("DELETE FROM routed_selections WHERE run_id=?", (rid,))
    vacancy = next(iter(jobs[0][1].values())).vacancy
    exclusions.record(store, vacancy, stage="evaluation", reason="old rejection")

    class Client:
        def evaluate(self, v):
            pytest.fail("excluded vacancy reached Decisions")

    exclusions.apply_run(store, rid)
    route_jobs(store, rid, frozen, Client())
    aggregate(store, rid)
    assert analysis.report(store, rid)["status"] == "complete"
    assert store.db.execute("SELECT status FROM run_jobs").fetchone()[0] == "EXCLUDED"
    assert store.db.execute("SELECT COUNT(*) FROM exclusion_displacements").fetchone()[0] == 1
    # A late stale result cannot resurrect it.
    store.save_outcome(rid, jobs[0][0], {"status": "ACCEPTED"})
    assert store.db.execute("SELECT status FROM run_jobs").fetchone()[0] == "EXCLUDED"
    store.close()


def test_database_guard_blocks_alternate_writer_reinserting_rejected_id(fixture):
    store, rid, frozen, jobs = prepare_routed(fixture, [["product"]])
    requests = list(jobs[0][1].values())
    vacancy = requests[0].vacancy
    exclusions.record(store, vacancy, stage="routing", reason="other")
    new = store.create_run(frozen, 100)
    store.add_job(new, requests, policy="decisions-one-two-1")
    assert (
        store.db.execute("SELECT COUNT(*) FROM run_jobs WHERE run_id=?", (new,)).fetchone()[0] == 0
    )
    store.close()


def test_collector_filter_sync_is_readonly_and_keeps_unknown(fixture):
    path = fixture[0] / "collector.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE vacancies(source,source_id,content_hash); "
        "CREATE TABLE filter_decisions(source,source_id,content_hash,policy_hash,payload);"
    )
    for ident, status in [("1", "FILTERED_OUT"), ("2", "REVIEW"), ("3", "KEEP")]:
        db.execute("INSERT INTO vacancies VALUES(?,?,?)", ("hh", ident, "hash"))
        db.execute(
            "INSERT INTO filter_decisions VALUES(?,?,?,?,?)",
            ("hh", ident, "hash", "policy", json.dumps({"status": status})),
        )
    db.commit()
    db.close()
    before = path.read_bytes()
    store = AnalysisStore(fixture[0] / "analysis.sqlite3")
    exclusions.sync_collector(store, path, {"hh": {"policy_hash": "policy"}})
    assert [r[0] for r in store.db.execute("SELECT source_id FROM vacancy_exclusions")] == ["1"]
    assert path.read_bytes() == before
    store.close()


def test_later_filter_change_cannot_undo_future_rejection(fixture):
    path = fixture[0] / "collector.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE vacancies(source,source_id,content_hash); "
        "CREATE TABLE filter_decisions(source,source_id,content_hash,policy_hash,payload);"
    )
    store = AnalysisStore(fixture[0] / "analysis.sqlite3")
    exclusions.sync_collector(store, path, {"hh": {"policy_hash": "original"}})
    for policy, status in [("earlier", "FILTERED_OUT"), ("current", "KEEP")]:
        db.execute(
            "INSERT INTO filter_decisions VALUES(?,?,?,?,?)",
            ("hh", "new-id", "hash", policy, json.dumps({"status": status})),
        )
    db.commit()
    db.close()
    exclusions.sync_collector(store, path, {"hh": {"policy_hash": "current"}})
    assert exclusions.identity_blocked(store.db, "hh", "new-id")
    store.close()


def test_assistant_catalog_filters_before_pagination(fixture):
    from career_scout import assistant_access as api

    store = AnalysisStore(fixture[0] / "analysis.sqlite3")
    exclusions.record_identity(store, "hh", "1", stage="routing", reason="other")
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        "CREATE TABLE vacancies(source,source_id,content_hash,detail_fetched_at); "
        "CREATE TABLE normalized_snapshots(source,source_id,content_hash,payload);"
    )
    for ident in ["1", "2", "3"]:
        db.execute("INSERT INTO vacancies VALUES(?,?,?,?)", ("hh", ident, "hash", "today"))
        db.execute(
            "INSERT INTO normalized_snapshots VALUES(?,?,?,?)",
            ("hh", ident, "hash", json.dumps({"source": "hh", "source_id": ident, "title": "job"})),
        )
    value = api.catalog(db, "", limit=1, analysis=store.db)
    assert value["total"] == 2
    assert value["items"][0]["source_id"] == "2"
    assert value["next_offset"] == 1
    db.close()
    store.close()


def test_migration_preserves_newer_accepted_card(fixture, monkeypatch):
    file, cfg, values = setup_config(fixture, monkeypatch)
    first = analysis.prepare(file, limit=1, max_calls=0)
    path = fixture[0] / "analysis.sqlite3"
    store = AnalysisStore(path)
    key = store.db.execute(
        "SELECT route_key FROM run_jobs WHERE run_id=?", (first["run_id"],)
    ).fetchone()[0]
    second = store.create_run(json.loads(store.run(first["run_id"])["config_json"]), 0)
    store.add_job(second, store.inputs(key), policy="decisions-one-two-1")
    for name in [
        r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
    ]:
        store.db.execute("DROP TRIGGER " + name)
    store.db.execute("DROP TABLE vacancy_exclusions")
    store.db.execute("DROP TABLE exclusion_bootstrap")
    store.save_outcome(first["run_id"], key, {"status": "REJECTED_BY_SELECTED"})
    store.save_outcome(second, key, {"status": "ACCEPTED"})
    store.close()
    store = AnalysisStore(path)
    assert not exclusions.identity_blocked(store.db, "hh", values[0]["source_id"])
    assert (
        store.db.execute("SELECT status FROM run_jobs WHERE run_id=?", (second,)).fetchone()[0]
        == "ACCEPTED"
    )
    store.close()


def test_failed_route_keeps_its_budget_reservation_for_resume(fixture):
    from career_scout.routed_pipeline import reserve_route

    store, rid, frozen, jobs = prepare_routed(fixture, [["product"]])
    with store.db:
        store.db.execute("INSERT INTO routing_budgets VALUES(?,?,0)", (rid, 9))
    reserve_route(store, rid, jobs[0][0], 9)
    reserve_route(store, rid, jobs[0][0], 9)
    assert store.db.execute("SELECT requests FROM routing_budgets").fetchone()[0] == 9
    store.close()
