"""Routed mode: independent dual evaluation, four queues and durable batch attempts."""

import copy
import json
import threading

import pytest
from test_analysis import fake_codex, result, wire_result
from test_analysis import setup as shared_setup

from career_scout.analysis import report
from career_scout.analysis_store import AnalysisStore
from career_scout.batch_worker import (
    BatchReply,
    CodexBatchWorker,
    batch_payload,
)
from career_scout.codex_worker import WorkerReply
from career_scout.common import digest
from career_scout.decisions import calibrate_margin, parse_decision, select_profiles
from career_scout.evaluation import ORDER, EvaluationError, make_input
from career_scout.routed_pipeline import aggregate, evaluate_queues, initialize, route_jobs


@pytest.fixture
def routed_setup(tmp_path):
    return shared_setup.__wrapped__(tmp_path)


def probs(**values):
    data = dict.fromkeys((*ORDER, "other"), 0.0)
    data.update(values)
    return data


def test_top_two_margin_boundary_and_no_predicate_questions():
    values = probs(product=0.5, automation=0.4, other=0.1)
    assert select_profiles(values, 0.099) == ["product"]
    assert select_profiles(values, 0.1) == ["product", "automation"]
    assert select_profiles(probs(product=0.5, automation=0.5), 0) == ["product", "automation"]
    assert select_profiles(probs(other=0.7, product=0.3), 1) == []
    assert select_profiles(probs(product=0.6, other=0.39, automation=0.01), 0.2) == ["product"]
    for value in [float("nan"), -0.1, 1.01]:
        with pytest.raises(EvaluationError):
            select_profiles(values, value)
    for malformed in [probs(product=float("nan")), probs(product=1.2), {"product": 1}]:
        with pytest.raises(EvaluationError):
            select_profiles(malformed, 0.1)


def test_native_distribution_validation():
    payload = {
        "model": "gpt-6-luna",
        "answers": [
            {
                "type": "choice",
                "name": "primary",
                "choice": "product",
                "probabilities": [
                    {"value": p, "probability": v} for p, v in probs(product=1).items()
                ],
            }
        ],
    }
    assert parse_decision(payload)["product"] == 1
    payload["answers"][0]["probabilities"].append({"value": "product", "probability": 0})
    with pytest.raises(EvaluationError, match="invalid_decisions_response"):
        parse_decision(payload)


def test_holdout_labels_never_choose_margin():
    development = [
        {
            "primary_profile": "product",
            "expected_profiles": ["product", "automation"],
            "probabilities": probs(product=0.55, automation=0.45),
        }
    ]
    heldout = [{**development[0], "expected_profiles": ["product"]}]
    first = calibrate_margin(development, heldout)
    second = calibrate_margin(development, [{**heldout[0], "expected_profiles": []}])
    assert first["margin"] == second["margin"] == 0.1
    assert first["holdout"] != second["holdout"]


def clone(request, number, *, description=None, config=None):
    vacancy = request.vacancy.model_copy(
        update={"source_id": str(number), **({"description": description} if description else {})}
    )
    vacancy.normalization_hash = digest(
        {
            "source": vacancy.source,
            "source_id": vacancy.source_id,
            "content_hash": vacancy.content_hash,
            "normalizer": vacancy.normalizer_version,
            "attributes": vacancy.attributes.model_dump(mode="json"),
        }
    )
    return make_input(
        vacancy=vacancy,
        resume=request.resume,
        filters=request.filters,
        filter_version=request.filter_version,
        rubric=request.rubric,
        rubric_version=request.rubric_version,
        common_instructions=request.common_instructions,
        config=config or type_config(request),
        evaluation_date=request.evaluation_date,
    )


def type_config(request):
    # Fixture uses the default config. Explicit changed configs are passed to clone.
    from career_scout.evaluation import EvaluatorConfig

    return EvaluatorConfig()


def batch_wire(requests):
    values = []
    for i, request in enumerate(requests):
        wire = wire_result(request)
        for a in wire["assessments"]:
            for k in ("from", "to"):
                a["vacancy_reference"][k] = "B" + str(i) + a["vacancy_reference"][k]
        values.append(wire)
    return {"results": values}


def test_ten_full_cards_one_cv_and_separate_source_ids(routed_setup):
    base = routed_setup[2]["product"]
    requests = [clone(base, i) for i in range(10)]
    prompt, schema = batch_payload(requests)
    data = json.loads(prompt.rsplit("\n\n", 1)[1])
    assert len(data["cases"]) == 10
    assert "".join(x["text"] for x in data["resume"]["evidence"]) == base.resume.text
    assert sum("resume" in case for case in data["cases"]) == 0
    for i, case in enumerate(data["cases"]):
        assert requests[i].vacancy.description in "".join(
            x["text"] for x in case["vacancy"]["evidence"]
        )
        assert case["vacancy"]["evidence"][0]["id"].startswith(f"B{i}V")
    assert len(schema["properties"]["results"]["items"]["anyOf"]) == 10
    with pytest.raises(EvaluationError, match="mixed_profile_batch"):
        batch_payload([requests[0], routed_setup[2]["automation"]])
    with pytest.raises(EvaluationError, match="invalid_batch_size"):
        batch_payload(requests + [clone(base, 11)])


def test_native_batch_isolates_cross_case_quote_error(routed_setup):
    path, config, profiles = routed_setup
    requests = [clone(profiles["product"], i) for i in range(10)]
    wire = batch_wire(requests)
    wire["results"][3]["assessments"][0]["vacancy_reference"]["from"] = "B4V0001"
    binary = fake_codex(
        path,
        "target=Path(sys.argv[sys.argv.index('--output-last-message')+1])\n"
        f"target.write_text(json.dumps({wire!r}))\n"
        "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':42}}))\n",
    )
    reply = CodexBatchWorker(config, binary=binary).batch(requests)
    assert len(reply.replies) == 9 and reply.usage == {"input_tokens": 42}
    assert reply.errors == {requests[3].evaluation_key: "unknown_evidence_reference"}


@pytest.mark.parametrize("kind", ["duplicate", "missing"])
def test_missing_or_duplicate_item_is_isolated(routed_setup, monkeypatch, kind):
    requests = [clone(routed_setup[2]["product"], i) for i in range(3)]
    wire = batch_wire(requests)
    wire["results"].pop(1)
    if kind == "duplicate":
        wire["results"].append(copy.deepcopy(wire["results"][0]))
    worker = CodexBatchWorker(routed_setup[1])
    monkeypatch.setattr(worker, "invoke", lambda *args: (wire, {}))
    reply = worker.batch(requests)
    assert requests[1].evaluation_key in reply.errors
    assert requests[2].evaluation_key in reply.replies
    assert (requests[0].evaluation_key in reply.errors) == (kind == "duplicate")


def prepare_routed(routed_setup, selections, *, max_calls=20, config=None):
    path, default, profiles = routed_setup
    config = config or default
    store = AnalysisStore(path / "routed.sqlite3")
    frozen = {
        "model": config.model_dump(mode="json"),
        "routing": {"margin": 0.1, "max_requests": 100},
    }
    run = store.create_run(frozen, max_calls)
    initialize(store)
    jobs = []
    for n, selected in enumerate(selections):
        requests = [clone(profiles[p], n, config=config) for p in ORDER]
        key = store.add_job(run, requests, policy="decisions-one-two-1")
        store.db.execute(
            "INSERT INTO routed_selections VALUES(?,?,?,?)", (run, key, "{}", json.dumps(selected))
        )
        jobs.append((key, {r.profile_id: r for r in requests}))
    store.db.commit()
    return store, run, frozen, jobs


def reply_for(requests, verdict="PASS", errors=None):
    errors = errors or {}
    return BatchReply(
        {
            r.evaluation_key: WorkerReply(result(r, verdict))
            for r in requests
            if r.evaluation_key not in errors
        },
        errors,
        {"input_tokens": 42},
        {},
    )


def test_four_workers_overlap_ten_each_and_resume_uses_cache(
    routed_setup,
):
    store, run, frozen, _ = prepare_routed(
        routed_setup, [[p] for p in ORDER for _ in range(10)], max_calls=4
    )
    barrier = threading.Barrier(4, timeout=15)
    calls = []
    owner = threading.get_ident()

    class Worker:
        def __init__(self, _):
            pass

        def cancel(self):
            pass

        def batch(self, requests):
            assert threading.get_ident() != owner
            calls.append((requests[0].profile_id, len(requests)))
            barrier.wait()
            return reply_for(requests)

    assert evaluate_queues(store, run, frozen, Worker) is None
    assert sorted(calls) == sorted((p, 10) for p in ORDER)
    assert report(store, run)["measured_tokens"]["input_tokens"] == 168
    assert report(store, run)["outcomes"] == {"ACCEPTED": 40}
    store.close()
    store = AnalysisStore(routed_setup[0] / "routed.sqlite3")
    assert evaluate_queues(store, run, frozen, Worker) is None
    assert len(calls) == 4
    store.close()


@pytest.mark.parametrize(
    ("product", "automation", "expected"),
    [
        ("PASS", "REJECT", "ACCEPTED"),
        ("REJECT", "REJECT", "REJECTED_BY_SELECTED"),
        ("BORDERLINE", "REJECT", "NEEDS_CLARIFICATION"),
    ],
)
def test_both_selected_workers_complete_even_after_pass(
    routed_setup, product, automation, expected
):
    store, run, frozen, jobs = prepare_routed(
        routed_setup, [["product", "automation"]], max_calls=2
    )
    barrier = threading.Barrier(2, timeout=15)

    class Worker:
        def __init__(self, _):
            pass

        def cancel(self):
            pass

        def batch(self, requests):
            barrier.wait()
            return reply_for(
                requests, {"product": product, "automation": automation}[requests[0].profile_id]
            )

    evaluate_queues(store, run, frozen, Worker)
    outcome = json.loads(store.db.execute("SELECT outcome_json FROM run_jobs").fetchone()[0])
    assert outcome["status"] == expected
    assert outcome["outcomes"]["business_analyst"]["verdict"] == "NOT_ROUTED"
    assert store.cached(jobs[0][1]["product"]) and store.cached(jobs[0][1]["automation"])
    store.close()


def test_incomplete_second_profile_keeps_run_partial(routed_setup):
    store, run, _, jobs = prepare_routed(routed_setup, [["product", "automation"]])
    from career_scout.evaluation import EvaluationResult

    request = jobs[0][1]["product"]
    store.save_result(request, EvaluationResult.model_validate(result(request)))
    aggregate(store, run)
    assert report(store, run)["outcomes"] == {"ACCEPTED": 1}
    assert report(store, run)["status"] == "partial"
    assert report(store, run)["incomplete_selected_evaluations"] == 1
    store.close()


def test_single_bad_case_retries_alone_with_one_usage_record(routed_setup):
    store, run, frozen, _ = prepare_routed(
        routed_setup, [["product"] for _ in range(10)], max_calls=2
    )
    calls = []

    class Worker:
        def __init__(self, _):
            pass

        def cancel(self):
            pass

        def batch(self, requests):
            calls.append(len(requests))
            errors = (
                {requests[3].evaluation_key: "invalid_evaluation_result"} if len(calls) == 1 else {}
            )
            return reply_for(requests, errors=errors)

    evaluate_queues(store, run, frozen, Worker)
    assert calls == [10, 1]
    assert report(store, run)["status"] == "complete"
    assert report(store, run)["measured_tokens"]["input_tokens"] == 84
    store.close()


def test_budget_is_durable_and_two_rejects_do_not_become_global_reject(routed_setup):
    store, run, frozen, _ = prepare_routed(
        routed_setup, [["product"] for _ in range(11)], max_calls=1
    )
    calls = []

    class Worker:
        def __init__(self, _):
            pass

        def cancel(self):
            pass

        def batch(self, requests):
            calls.append(len(requests))
            return reply_for(requests, "REJECT")

    assert evaluate_queues(store, run, frozen, Worker) == "analysis_call_budget_exhausted"
    assert calls == [10] and report(store, run)["status"] == "partial"
    assert evaluate_queues(store, run, frozen, Worker) == "analysis_call_budget_exhausted"
    store.retry(run, 1)
    assert evaluate_queues(store, run, frozen, Worker) is None
    assert calls == [10, 1] and report(store, run)["outcomes"] == {"REJECTED_BY_SELECTED": 11}
    store.close()


def test_oversized_first_ten_do_not_starve_later_cases(routed_setup, monkeypatch):
    import career_scout.routed_pipeline as pipeline

    store, run, frozen, jobs = prepare_routed(routed_setup, [["product"] for _ in range(11)])
    badkeys = {items["product"].evaluation_key for _, items in jobs[:10]}
    monkeypatch.setattr(
        pipeline,
        "batch_size_characters",
        lambda rs: 200000 if any(r.evaluation_key in badkeys for r in rs) else 100,
    )
    calls = []

    class Worker:
        def __init__(self, _):
            pass

        def cancel(self):
            pass

        def batch(self, requests):
            calls.append(len(requests))
            return reply_for(requests)

    evaluate_queues(store, run, frozen, Worker)
    assert calls == [1] and report(store, run)["outcomes"] == {"PENDING": 10, "ACCEPTED": 1}
    assert store.db.execute("SELECT COUNT(*) FROM routed_input_errors").fetchone()[0] == 10
    store.close()


def test_router_records_its_selection_once_and_other_goes_to_review(routed_setup):
    store, run, frozen, jobs = prepare_routed(routed_setup, [[]])
    store.db.execute("DELETE FROM routed_selections")
    store.db.commit()

    class Client:
        calls = 0

        def evaluate(self, v):
            self.calls += 1
            return {"probabilities": probs(other=1)}

    client = Client()
    route_jobs(store, run, frozen, client)
    route_jobs(store, run, frozen, client)
    aggregate(store, run)
    assert client.calls == 1 and report(store, run)["outcomes"] == {"ROUTING_REVIEW": 1}
    assert store.db.execute("SELECT COUNT(*) FROM inputs").fetchone()[0] == 4
    store.close()


def test_native_connector_receipt_cache_never_repeats_paid_request(routed_setup, monkeypatch):
    import sys

    import career_scout.decisions as decisions

    path, _, requests = routed_setup
    monkeypatch.setattr(decisions, "NATIVE_ROOT", path)
    executable = path / "native-helper"
    sentinel = path / "called"
    executable.write_text(
        f"#!{sys.executable}\nimport sys,json\nfrom pathlib import Path\n"
        f"Path({str(sentinel)!r}).open('a').write('1')\n"
        "target=Path(sys.argv[sys.argv.index('--output')+1])\n"
        f"values={probs(product=1)!r}\n"
        "response={'model':'gpt-6-luna','answers':[{'type':'choice','name':'primary',"
        "'choice':'product','probabilities':"
        "[{'value':k,'probability':v} for k,v in values.items()]}]}\n"
        "target.write_text(json.dumps({'ok':True,'http_status':200,'response':response}))\n"
    )
    executable.chmod(0o700)
    client = decisions.DecisionsClient(path, binary=str(executable))
    first = client.evaluate(requests["product"].vacancy)
    second = client.evaluate(requests["product"].vacancy)
    assert first["cached"] is False and second["cached"] is True
    assert sentinel.read_text() == "1"
    receipt = path / (first["key"] + "-receipt.json")
    receipt.write_text('{"ok":false,"http_status":500}')
    with pytest.raises(EvaluationError, match="decisions_failed_or_unknown"):
        client.evaluate(requests["product"].vacancy)
    assert sentinel.read_text() == "1"
    with pytest.raises(EvaluationError, match="invalid_decisions_workspace"):
        decisions.DecisionsClient(path.parent)


def test_preparation_freezes_operating_margin_and_routing_namespace(routed_setup, monkeypatch):
    from pathlib import Path

    from career_scout import analysis
    from career_scout.decisions import INSTRUCTIONS, MODEL, PROFILES

    path, _, requests = routed_setup
    cfg = json.loads(Path("configs/analysis.routed.example.json").read_text())
    assert cfg["external_calls_enabled"] is False and cfg["routing"]["max_requests"] == 0
    for p in ORDER:
        target = path / (p + ".json")
        target.write_text(requests[p].resume.model_dump_json())
        cfg["profiles"][p] = str(target)
        cfg["rubrics"][p] = str(Path("prompts/" + p + ".md").resolve())
    calibration = path / "calibration.json"
    calibration.write_text(
        json.dumps(
            {
                "policy_hash": digest(
                    {"model": MODEL, "instructions": INSTRUCTIONS, "profiles": PROFILES}
                ),
                "margin": 0.1,
            }
        )
    )
    cfg.update(
        collector_database=str(path / "collector.sqlite3"),
        database=str(path / "new.sqlite3"),
        common_prompt=str(Path("prompts/common.md").resolve()),
    )
    cfg["routing"].update(calibration_file=str(calibration), workspace=str(path))
    configfile = path / "config.json"
    configfile.write_text(json.dumps(cfg))
    vacancy = requests["product"].vacancy.model_dump(mode="json")
    monkeypatch.setattr(
        analysis,
        "read_catalog",
        lambda *args: ([vacancy], {"hh": {"policy_json": "{}", "policy_hash": "0" * 64}}),
    )
    value = analysis.prepare(configfile, limit=100, max_calls=4)
    store = AnalysisStore(path / "new.sqlite3")
    frozen = json.loads(store.run(value["run_id"])["config_json"])
    assert frozen["routing"]["margin"] == 0.1
    assert (
        json.loads(store.db.execute("SELECT manifest_json FROM jobs").fetchone()[0])["policy"]
        == "decisions-one-two-1"
    )
    store.close()
    calibration.write_text('{"policy_hash":"stale","margin":0.1}')
    with pytest.raises(EvaluationError, match="invalid_routing_calibration"):
        analysis.prepare(configfile, limit=100, max_calls=4)


def test_routed_export_does_not_leak_unselected_legacy_cached_results(routed_setup):
    from career_scout.analysis import export_results
    from career_scout.evaluation import EvaluationResult

    store, run, _, jobs = prepare_routed(routed_setup, [["product"]])
    for request in jobs[0][1].values():
        store.save_result(request, EvaluationResult.model_validate(result(request)))
    path = routed_setup[0] / "export.json"
    export_results(store, run, path)
    payload = json.loads(path.read_text())
    assert set(payload["vacancies"][0]["evaluations"]) == {"product"}
    assert store.db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 4
    store.close()
