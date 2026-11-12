"""Finite source phases, immutable boundaries and crash-safe admission reuse."""

import json
from collections import Counter

import pytest
from test_analysis import setup as base_setup
from test_applications import setup_config

from career_scout import analysis, array_analysis
from career_scout.analysis_store import AnalysisStore
from career_scout.common import digest
from career_scout.evaluation import EvaluationError


@pytest.fixture
def array_fixture(tmp_path, monkeypatch):
    fixture = base_setup.__wrapped__(tmp_path)
    config, cfg, values = setup_config(fixture, monkeypatch)
    cfg["routing"]["max_requests"] = 100
    config.write_text(json.dumps(cfg))
    vacancies = []
    for source, count in [("habr", 2), ("hh", 3), ("superjob", 1), ("trudvsem", 1)]:
        for index in range(count):
            value = {**values[index], "source": source}
            if source == "trudvsem":
                value["source_id"] = json.dumps(["company", str(index)], separators=(",", ":"))
            value["normalization_hash"] = digest(
                {
                    "source": source,
                    "source_id": value["source_id"],
                    "content_hash": value["content_hash"],
                    "normalizer": value["normalizer_version"],
                    "attributes": value["attributes"],
                }
            )
            vacancies.append(value)
    catalog = {
        "schema_version": 1,
        "campaign_id": cfg["campaign_id"],
        "source_order": list(array_analysis.SOURCE_ORDER),
        "vacancies": vacancies,
        "policies": {
            s: {"policy_json": "{}", "policy_hash": "0" * 64} for s in array_analysis.SOURCE_ORDER
        },
    }
    catalog["content_hash"] = digest(catalog)
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    output = tmp_path / "pass"
    calls = []

    def evaluate(store, run_id, **kwargs):
        sources = {
            store.inputs(r[0])[0].vacancy.source
            for r in store.db.execute("SELECT route_key FROM run_jobs WHERE run_id=?", (run_id,))
        }
        assert len(sources) == 1
        calls.extend(sources)
        with store.db:
            store.db.execute(
                "UPDATE run_jobs SET status='ROUTING_REVIEW',outcome_json=? WHERE run_id=?",
                (json.dumps({"selected_profiles": []}), run_id),
            )
        return analysis.report(store, run_id)

    monkeypatch.setattr(array_analysis, "run_pending", evaluate)
    return config, path, output, calls, catalog


def test_all_sources_once_and_restart_has_no_calls(array_fixture):
    config, path, output, calls, catalog = array_fixture
    result = array_analysis.execute(config, path, output, chunk_size=2)
    assert result["status"] == "complete"
    assert calls == ["hh", "hh", "habr", "superjob", "trudvsem"]
    assert sum(t["count"] for t in result["tasks"]) == 7
    assert result["by_source"] == {"hh": 3, "habr": 2, "superjob": 1, "trudvsem": 1}
    array_analysis.execute(config, path, output, chunk_size=2)
    assert len(calls) == 5
    store = AnalysisStore(config.parent / "analysis.sqlite3")
    assert store.db.execute("SELECT COUNT(*) FROM analysis_admissions").fetchone()[0] == 7
    assert store.db.execute("SELECT COUNT(*) FROM run_jobs").fetchone()[0] == 7
    store.close()


def test_reuses_previous_pending_and_completed_jobs(array_fixture, monkeypatch):
    config, path, output, calls, catalog = array_fixture
    values = [v for v in catalog["vacancies"] if v["source"] == "hh"][:2]
    previous = analysis.prepare(config, limit=2, max_calls=0, catalog=(values, catalog["policies"]))
    result = array_analysis.execute(config, path, output, chunk_size=2)
    assert result["tasks"][0]["new_jobs"] == 0
    assert result["tasks"][0]["reused_jobs"] == 2
    assert previous["run_id"] in result["tasks"][0]["runs"]
    assert Counter(calls)["hh"] == 2
    store = AnalysisStore(config.parent / "analysis.sqlite3")
    assert store.db.execute("SELECT COUNT(*) FROM run_jobs").fetchone()[0] == 7
    store.close()


def test_fatal_stops_before_next_source_then_resumes_same_admissions(array_fixture, monkeypatch):
    config, path, output, calls, catalog = array_fixture
    evaluate = array_analysis.run_pending
    monkeypatch.setattr(
        array_analysis,
        "run_pending",
        lambda store, rid, **kw: {**analysis.report(store, rid), "error": "provider_unavailable"},
    )
    first = array_analysis.execute(config, path, output, chunk_size=2)
    assert first["status"] == "blocked"
    assert first["active_source"] == "hh"
    run_id = first["tasks"][0]["runs"][0]
    monkeypatch.setattr(array_analysis, "run_pending", evaluate)
    resumed = array_analysis.execute(config, path, output, chunk_size=2)
    assert resumed["status"] == "complete"
    assert resumed["tasks"][0]["runs"][0] == run_id
    assert calls == ["hh", "hh", "habr", "superjob", "trudvsem"]


def test_frozen_boundary_and_config_cannot_silently_change(array_fixture):
    config, path, output, calls, catalog = array_fixture
    array_analysis.execute(config, path, output, chunk_size=2)
    changed = json.loads(config.read_text())
    changed["owner_attributes"] = {"location": "changed"}
    config.write_text(json.dumps(changed))
    with pytest.raises(EvaluationError, match="array_configuration_changed"):
        array_analysis.execute(config, path, output, chunk_size=2)
    catalog["vacancies"].pop()
    path.write_text(json.dumps(catalog))
    with pytest.raises(EvaluationError, match="array_catalog_changed"):
        array_analysis.load_catalog(path)
    assert len(calls) == 5


def test_duplicate_identities_rejected(array_fixture):
    config, path, output, calls, catalog = array_fixture
    catalog.pop("content_hash")
    catalog["vacancies"].append(catalog["vacancies"][0])
    catalog["content_hash"] = digest(catalog)
    path.write_text(json.dumps(catalog))
    with pytest.raises(EvaluationError, match="invalid_array_catalog_identities"):
        array_analysis.load_catalog(path)
    assert not calls


def test_exhausted_items_remain_errors_not_rejections(array_fixture, monkeypatch):
    config, path, output, calls, catalog = array_fixture
    monkeypatch.setattr(
        array_analysis, "run_pending", lambda store, rid, **kw: analysis.report(store, rid)
    )
    state = array_analysis.execute(config, path, output, chunk_size=2)
    assert state["status"] == "complete_with_errors"
    assert all(t["status"] == "incomplete_evaluations" for t in state["tasks"])
    assert state["recommended"] == 0


def test_freeze_is_read_only_boundary_and_refuses_overwrite(array_fixture, monkeypatch):
    config, path, output, calls, catalog = array_fixture
    monkeypatch.setattr(
        array_analysis, "read_catalog", lambda *a: (catalog["vacancies"], catalog["policies"])
    )
    frozen = config.parent / "frozen.json"
    result = array_analysis.freeze_catalog(config, frozen)
    assert result["total"] == 7
    before = frozen.read_bytes()
    with pytest.raises(FileExistsError):
        array_analysis.freeze_catalog(config, frozen)
    assert frozen.read_bytes() == before
    assert array_analysis.load_catalog(frozen)["vacancies"] == catalog["vacancies"]


def test_crash_after_admission_before_checkpoint_reuses_run(array_fixture, monkeypatch):
    config, path, output, calls, catalog = array_fixture
    prepare = array_analysis.prepare
    first = True

    def interrupted(*a, **kw):
        nonlocal first
        result = prepare(*a, **kw)
        if first:
            first = False
            raise RuntimeError("simulated process death after admission")
        return result

    monkeypatch.setattr(array_analysis, "prepare", interrupted)
    with pytest.raises(RuntimeError, match="simulated process death"):
        array_analysis.execute(config, path, output, chunk_size=2)
    result = array_analysis.execute(config, path, output, chunk_size=2)
    assert result["status"] == "complete"
    assert result["tasks"][0]["reused_jobs"] == 2
    store = AnalysisStore(config.parent / "analysis.sqlite3")
    assert store.db.execute("SELECT COUNT(*) FROM run_jobs").fetchone()[0] == 7
    store.close()


def test_old_mixed_pending_run_cannot_bypass_source_order(array_fixture):
    config, path, output, calls, catalog = array_fixture
    values = [next(v for v in catalog["vacancies"] if v["source"] == s) for s in ("hh", "habr")]
    analysis.prepare(config, limit=2, max_calls=0, catalog=(values, catalog["policies"]))
    state = array_analysis.execute(config, path, output, chunk_size=2)
    assert state["status"] == "blocked"
    assert state["error"] == "pending_run_crosses_source_phase"
    assert calls == []
