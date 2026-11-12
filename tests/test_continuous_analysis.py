"""Continuous refill, per-profile exclusivity, exact ceiling and safe resumption."""

import collections
import json
import threading

import pytest
from test_applications import app_result
from test_array_analysis import array_fixture as base_array_fixture

from career_scout import array_analysis, decisions, routed_pipeline
from career_scout.analysis_store import AnalysisStore
from career_scout.batch_worker import BatchReply
from career_scout.codex_worker import WorkerReply
from career_scout.evaluation import EvaluationError
from career_scout.routed_pipeline import evaluate_queues as evaluate_all


@pytest.fixture
def continuous_fixture(tmp_path, monkeypatch):
    values = base_array_fixture.__wrapped__(tmp_path, monkeypatch)
    config = values[0]
    cfg = json.loads(config.read_text())
    cfg["external_calls_enabled"] = True
    config.write_text(json.dumps(cfg))
    return values


def continuous_workers(monkeypatch, *, overlap=False, fail_route=None, shared=False):
    calls, routed = [], []
    owner = threading.get_ident()
    release = threading.Event()
    active = collections.Counter()
    guard = threading.Lock()
    actual = evaluate_all

    class Worker:
        def __init__(self, _):
            pass

        def cancel(self):
            release.set()

        def batch(self, requests):
            assert threading.get_ident() != owner
            p = requests[0].profile_id
            with guard:
                if not shared:
                    assert p not in active
                active[p] += 1
                assert sum(active.values()) <= 4
            try:
                source = requests[0].vacancy.source
                ids = [r.vacancy.source_id for r in requests]
                calls.append((source, p, ids))
                if overlap and source == "hh" and p == "product":
                    assert release.wait(10), "next-portion worker was blocked by slow product"
                if overlap and source == "hh" and "2" in ids:
                    assert "product" in active
                    release.set()
                return BatchReply(
                    {r.evaluation_key: WorkerReply(app_result(r)) for r in requests},
                    {},
                    {"input_tokens": 1},
                    {},
                )
            finally:
                with guard:
                    active[p] -= 1
                    if not active[p]:
                        del active[p]

    def route(store, run_id, frozen, client):
        assert threading.get_ident() == owner
        rows = store.db.execute("SELECT route_key FROM run_jobs WHERE run_id=?", (run_id,))
        for row in rows:
            request = store.inputs(row[0])[0]
            source, ident = request.vacancy.source, request.vacancy.source_id
            if source != "hh":
                assert not active, "later source started before preceding workers drained"
            routed.append((source, ident))
            if fail_route and len(routed) == fail_route:
                raise EvaluationError("decisions_outcome_unknown", fatal=True)
            p = "product" if source == "hh" and ident == "0" else "automation"
            with store.db:
                store.db.execute(
                    "INSERT OR IGNORE INTO routed_selections VALUES(?,?,?,?)",
                    (run_id, row[0], "{}", json.dumps([p])),
                )

    monkeypatch.setattr(decisions, "DecisionsClient", lambda *a, **kw: object())
    monkeypatch.setattr(routed_pipeline, "route_jobs", route)
    monkeypatch.setattr(
        routed_pipeline,
        "evaluate_queues",
        lambda *a, **kw: actual(*a, worker_factory=Worker, **kw),
    )
    return calls, routed


def test_refill_crosses_portions_while_slow_profile_runs_and_respects_sources(
    continuous_fixture,
    monkeypatch,
):
    config, path, output, _, _ = continuous_fixture
    calls, routed = continuous_workers(monkeypatch, overlap=True)
    state = array_analysis.execute(config, path, output, chunk_size=2, continuous=True)
    assert state["status"] == "complete"
    assert [s for s, _ in routed] == ["hh"] * 3 + ["habr"] * 2 + ["superjob", "trudvsem"]
    assert sum(len(ids) for _, _, ids in calls) == 7
    before = list(calls)
    again = array_analysis.execute(config, path, output, chunk_size=2, continuous=True)
    assert again["status"] == "complete" and calls == before


@pytest.mark.parametrize("limit", [1, 2, 3, 4])
def test_exact_limit_is_durable_across_restart_then_can_be_raised(
    continuous_fixture,
    monkeypatch,
    limit,
):
    config, path, output, _, _ = continuous_fixture
    calls, routed = continuous_workers(monkeypatch)
    state = array_analysis.execute(
        config,
        path,
        output,
        chunk_size=2,
        continuous=True,
        stop_after=limit,
    )
    assert state["status"] == "stopped_at_limit"
    assert state["completed_catalog_count"] == limit
    assert len(routed) == limit
    assert sum(len(ids) for _, _, ids in calls) == limit
    before = list(calls)
    # Omitting the flag must not silently resume past a stored ceiling.
    state = array_analysis.execute(config, path, output, chunk_size=2, continuous=True)
    assert state["status"] == "stopped_at_limit" and calls == before
    with pytest.raises(EvaluationError, match="stop_limit_requires_continuous_mode"):
        array_analysis.execute(config, path, output, chunk_size=2)
    state = array_analysis.execute(
        config,
        path,
        output,
        chunk_size=2,
        continuous=True,
        stop_after=7,
    )
    assert state["status"] == "complete"
    assert len(routed) == 7
    store = AnalysisStore(config.parent / "analysis.sqlite3")
    assert store.db.execute("SELECT COUNT(*) FROM analysis_admissions").fetchone()[0] == 7
    assert store.db.execute("SELECT COUNT(*) FROM run_jobs").fetchone()[0] == 7
    store.close()


def test_routing_failure_drains_active_workers_preserves_results_and_resumes(
    continuous_fixture,
    monkeypatch,
):
    config, path, output, _, _ = continuous_fixture
    calls, routed = continuous_workers(monkeypatch, fail_route=3)
    state = array_analysis.execute(
        config,
        path,
        output,
        chunk_size=2,
        continuous=True,
        stop_after=3,
    )
    assert state["status"] == "blocked" and state["error"] == "decisions_outcome_unknown"
    assert sum(len(ids) for _, _, ids in calls) == 2
    old_runs = [r for t in state["tasks"] for r in t.get("runs", [])]
    new_calls, _ = continuous_workers(monkeypatch)
    state = array_analysis.execute(config, path, output, chunk_size=2, continuous=True)
    assert state["status"] == "stopped_at_limit"
    assert sum(len(ids) for _, _, ids in new_calls) == 1
    assert old_runs == [r for t in state["tasks"] for r in t.get("runs", [])]


def test_limit_cannot_hide_previously_admitted_work(continuous_fixture, monkeypatch):
    config, path, output, _, _ = continuous_fixture
    continuous_workers(monkeypatch)
    array_analysis.execute(config, path, output, chunk_size=2, continuous=True, stop_after=3)
    with pytest.raises(EvaluationError, match="stop_limit_(crosses|below)_admitted"):
        array_analysis.execute(config, path, output, chunk_size=2, continuous=True, stop_after=1)


def test_shared_pool_keeps_source_barrier_and_exact_limit(continuous_fixture, monkeypatch):
    config, path, output, _, _ = continuous_fixture
    calls, routed = continuous_workers(monkeypatch, shared=True)
    state = array_analysis.execute(
        config, path, output, chunk_size=2, continuous=True, stop_after=4, shared_slots=True
    )
    assert state["status"] == "stopped_at_limit" and state["shared_worker_slots"] is True
    assert [source for source, _ in routed] == ["hh", "hh", "hh", "habr"]
    assert sum(len(ids) for _, _, ids in calls) == 4
