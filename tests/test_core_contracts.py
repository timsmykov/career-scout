"""Portable core acceptance: source drift, units, proofs, queues and connections."""

import copy
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from referencing import Registry, Resource
from test_analysis import fake_codex, result
from test_analysis import setup as base_setup
from test_applications import app_input, app_result
from test_routed_analysis import batch_wire, clone
from test_sources import client, listing

from career_scout import decisions, routed_pipeline
from career_scout.analysis_store import AnalysisStore
from career_scout.batch_worker import BatchReply, CodexBatchWorker
from career_scout.codex_worker import CodexWorker, WorkerReply
from career_scout.common import SourceError, digest, strict_json
from career_scout.data_quality import inspect_quality, salary_facts, shape
from career_scout.evaluation import EvaluationError, EvaluatorConfig, make_input
from career_scout.evidence import EvidenceDocument
from career_scout.hh import Credentials, HHClient
from career_scout.models import Salary
from career_scout.source_types import Page


@pytest.fixture
def fixture(tmp_path):
    return base_setup.__wrapped__(tmp_path)


@pytest.mark.parametrize(
    "text", ['{"a":1,"a":2}', '{"x":{"a":1,"a":2}}', "NaN", "Infinity", "1e999"]
)
def test_ambiguous_json_is_not_accepted(text):
    with pytest.raises(ValueError):
        strict_json(text)


def test_strict_json_preserves_normal_unicode_and_numbers():
    assert strict_json('{"a":[1,1.5,null,true,"Привет"]}') == {"a": [1, 1.5, None, True, "Привет"]}


@pytest.mark.parametrize(
    "raw", [b'{"found":0,"found":1}', b'{"items":[null],"found":1,"pages":1,"page":0,"per_page":1}']
)
def test_hh_drift_is_a_safe_source_failure(tmp_path, raw):
    env = tmp_path / "hh.env"
    env.write_text("HH_ACCESS_TOKEN=fake\nHH_USER_AGENT=SyntheticTest\n")
    transport = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=raw))
    )
    source = HHClient(Credentials(env), client=transport, interval=0, retries=0)
    with pytest.raises(SourceError, match="invalid_json|schema_drift"):
        source.search("test", None, None, 0, 1)


def test_codex_transport_rejects_ambiguous_json(fixture):
    path, config, requests = fixture
    binary = fake_codex(
        path,
        "target=Path(sys.argv[sys.argv.index('--output-last-message')+1])\n"
        'target.write_text(\'{"a":1,"a":2}\')\n',
    )
    with pytest.raises(EvaluationError, match="codex_invalid_json"):
        CodexWorker(config, binary=binary)(requests["product"])


@pytest.mark.parametrize("value", [None, 1, "", " ", True])
def test_page_cannot_admit_missing_or_changed_id_types(value):
    with pytest.raises(ValidationError):
        Page(items=[{"id": value}], found=1, pages=1, page=0, per_page=1)


@pytest.mark.parametrize("source", ["trudvsem", "superjob", "habr"])
def test_source_contract_drift_is_safe_and_never_an_empty_success(source, tmp_path):
    payload = listing(source, 0, [1], total=1, per_page=1)
    field = {"superjob": "objects", "habr": "list"}.get(source)
    if field:
        payload[field] = ["unexpected-source-shape"]
    else:
        payload["results"]["vacancies"] = ["unexpected-source-shape"]
    c = client(source, tmp_path, lambda r: httpx.Response(200, json=payload))
    with pytest.raises(SourceError, match="schema_drift"):
        c.search("example", None, None, 0, 1)


@pytest.mark.parametrize("source", ["trudvsem", "superjob", "habr"])
def test_source_optional_addition_does_not_break_or_discard_input(source, tmp_path):
    payload = listing(source, 0, [1], total=1, per_page=1)
    payload["new_optional_metadata"] = {"version": 2}
    c = client(source, tmp_path, lambda r: httpx.Response(200, json=payload))
    assert len(c.search("example", None, None, 0, 1).items) == 1


@pytest.mark.parametrize("source", ["trudvsem", "superjob", "habr"])
def test_sources_reject_duplicate_json_without_returning_a_partial_body(source, tmp_path):
    c = client(source, tmp_path, lambda r: httpx.Response(200, content=b'{"total":1,"total":2}'))
    with pytest.raises(SourceError, match="invalid_json"):
        c.search("example", None, None, 0, 1)


def test_money_units_tax_and_frequency_are_independent_and_non_destructive():
    s = Salary(
        amount_from=80000,
        currency="RUB",
        period="month",
        gross=False,
        payout_frequency="twice_monthly",
    )
    original = s.model_dump()
    facts = salary_facts(s)
    assert facts["monthly"]["from"] == "80000" and facts["status"] == "known"
    assert facts["monthly"]["gross"] is False and s.model_dump() == original
    s.period = "hour"
    assert salary_facts(s)["monthly"] is None
    s.period = None
    assert salary_facts(s)["monthly"] is None  # Monthly payout cannot invent a rate period.
    s.period = "year"
    assert Decimal(salary_facts(s)["monthly"]["from"]) == Decimal(80000) / 12


def test_unknown_conflicting_and_zero_salary_are_distinct():
    assert salary_facts(None)["status"] == "unknown"
    unknown = salary_facts(Salary(amount_from=1))
    assert unknown["status"] == "partial" and "salary.gross_unknown" in unknown["reasons"]
    zero = salary_facts(Salary(amount_from=0, currency="RUB", gross=False, period="month"))
    assert zero["monthly"]["from"] == "0"
    conflict = Salary(amount_from=100000, amount_to=50000, currency="USD", period="month")
    assert salary_facts(conflict)["status"] == "conflict"
    assert salary_facts(conflict)["monthly"] is None and conflict.amount_from == 100000


def test_quality_projection_preserves_snapshot_and_shape_contains_no_values(fixture):
    vacancy = fixture[2]["product"].vacancy.model_dump(mode="json")
    original = copy.deepcopy(vacancy)
    report = inspect_quality(vacancy)
    assert vacancy == original and report["source"] == vacancy["source"]
    assert shape({"field": "SECRET_VALUE"}) == {"field": "str"}
    assert "SECRET_VALUE" not in json.dumps(shape({"field": "SECRET_VALUE"}))


@pytest.mark.parametrize("bad", [[], {}, 1, None])
def test_bad_evidence_identifiers_are_validation_errors_not_python_failures(bad):
    with pytest.raises(EvaluationError, match="invalid_evidence_reference"):
        EvidenceDocument("Source text.", "V").resolve({"from": bad, "to": "V0001"})


@pytest.mark.parametrize("wire", [None, [], {"results": {}}, {"results": [], "extra": 1}])
def test_invalid_batch_container_is_technical_error(fixture, monkeypatch, wire):
    worker = CodexBatchWorker(fixture[1])
    monkeypatch.setattr(worker, "invoke", lambda *a: (wire, {}))
    with pytest.raises(EvaluationError, match="invalid_batch_envelope"):
        worker.batch([fixture[2]["product"]])


def test_foreign_result_key_cannot_enter_a_batch(fixture, monkeypatch):
    request = fixture[2]["product"]
    wire = batch_wire([request])
    wire["results"][0]["evaluation_key"] = "a" * 64
    worker = CodexBatchWorker(fixture[1])
    monkeypatch.setattr(worker, "invoke", lambda *a: (wire, {}))
    with pytest.raises(EvaluationError, match="unexpected_batch_result"):
        worker.batch([request])


def test_legacy_model_hash_and_explicit_executable(fixture):
    config = fixture[1]
    values = config.model_dump(mode="json")
    assert "executable" not in values
    assert config.key() == digest(values)
    custom = EvaluatorConfig.model_validate({**values, "executable": "/custom/codex"})
    assert CodexWorker(custom).binary == "/custom/codex" and custom.key() != config.key()


def test_portable_decisions_connection_and_shared_receipt_lock(tmp_path, monkeypatch):
    workspace = tmp_path / "receipts"
    workspace.mkdir()
    settings = {
        "workspace": str(workspace),
        "workspace_root": str(tmp_path),
        "native_binary": "/custom/native",
    }
    clients = [decisions.DecisionsClient.from_settings(settings) for _ in range(2)]
    body = {"model": "test", "input": "synthetic"}
    calls = []

    def execute(args, **kw):
        calls.append(args[0])
        time.sleep(0.05)
        receipt = {"ok": True, "http_status": 200, "response": {"test": True}}
        from pathlib import Path

        Path(args[args.index("--output") + 1]).write_text(json.dumps(receipt))

    monkeypatch.setattr(decisions.subprocess, "run", execute)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda c: c.request(body), clients))
    assert calls == ["/custom/native"] and all(r["response"] == {"test": True} for r in replies)
    with pytest.raises(EvaluationError, match="invalid_decisions_workspace"):
        decisions.DecisionsClient(tmp_path, workspace_root=workspace)


@pytest.mark.parametrize("resumes,slots", [(1, 4), (2, 1), (7, 4)])
def test_queue_handles_arbitrary_resume_ids_independently_of_slots(fixture, resumes, slots):
    path, config, profiles = fixture
    base = app_input(profiles["product"], config)
    order = [f"resume_{i}" for i in range(resumes)]
    frozen = {
        "model": config.model_dump(mode="json"),
        "order": order,
        "routing": {"batch_size": 20, "workers": slots},
        "evaluation_policy": "applications-1",
    }
    store = AnalysisStore(path / "generic-queue.sqlite3")
    routed_pipeline.initialize(store)
    run = store.create_run(frozen, resumes)
    requests = []
    for i, name in enumerate(order):
        original = clone(base, i)
        resume = original.resume.model_copy(update={"profile_id": name})
        request = make_input(
            vacancy=original.vacancy,
            resume=resume,
            filters=original.filters,
            filter_version=original.filter_version,
            rubric=original.rubric,
            rubric_version=original.rubric_version,
            common_instructions=original.common_instructions,
            config=config,
            evaluation_date=original.evaluation_date,
        )
        requests.append(request)
        key = store.add_job(run, [request])
        store.db.execute(
            "INSERT INTO routed_selections VALUES(?,?,?,?)", (run, key, "{}", json.dumps([name]))
        )
    store.db.commit()
    calls = []

    class Worker:
        def __init__(self, _):
            pass

        def cancel(self):
            pass

        def batch(self, batch):
            calls.extend(r.profile_id for r in batch)
            return BatchReply(
                {r.evaluation_key: WorkerReply(app_result(r)) for r in batch}, {}, {}, {}
            )

    assert routed_pipeline.evaluate_queues(store, run, frozen, Worker, shared_slots=True) is None
    assert sorted(calls) == sorted(order) and all(
        store.cached(r).verdict == "PASS" for r in requests
    )
    assert routed_pipeline.evaluate_queues(store, run, frozen, Worker, shared_slots=True) is None
    assert len(calls) == resumes
    assert not store.db.execute("SELECT 1 FROM batch_attempts WHERE finished_at IS NULL").fetchone()
    store.close()


def test_unexpected_worker_error_is_finished_pending_and_sanitized(fixture):
    from test_routed_analysis import prepare_routed

    store, run, frozen, _ = prepare_routed(fixture, [["product"]])

    class Worker:
        def __init__(self, _):
            pass

        def cancel(self):
            pass

        def batch(self, _):
            raise RuntimeError("SECRET_FROM_PROVIDER")

    assert routed_pipeline.evaluate_queues(store, run, frozen, Worker) == "worker_execution_error"
    assert not store.db.execute("SELECT 1 FROM batch_attempts WHERE finished_at IS NULL").fetchone()
    assert store.db.execute("SELECT status FROM run_jobs").fetchone()[0] == "PENDING"
    assert "SECRET_FROM_PROVIDER" not in "\n".join(store.db.iterdump())
    store.close()


def test_invalid_neighbor_keeps_valid_result_and_does_not_become_rejection(fixture):
    from test_routed_analysis import prepare_routed

    config = EvaluatorConfig(max_attempts_per_profile=1)
    store, run, frozen, jobs = prepare_routed(fixture, [["product"], ["product"]], config=config)

    class Worker:
        def __init__(self, _):
            pass

        def cancel(self):
            pass

        def batch(self, requests):
            replies = {r.evaluation_key: WorkerReply(result(r)) for r in requests}
            replies[requests[1].evaluation_key].result["evaluation_key"] = "a" * 64
            return BatchReply(replies, {}, {}, {})

    assert routed_pipeline.evaluate_queues(store, run, frozen, Worker) is None
    assert store.cached(jobs[0][1]["product"]) is not None
    assert store.cached(jobs[1][1]["product"]) is None
    assert (
        store.db.execute("SELECT status FROM run_jobs ORDER BY position DESC").fetchone()[0]
        == "PENDING"
    )
    store.close()


def test_native_receipt_is_not_submitted_twice_by_separate_processes(tmp_path):
    workspace = tmp_path / "requests"
    workspace.mkdir()
    counter = tmp_path / "calls"
    binary = tmp_path / "native"
    binary.write_text(
        f"#!{sys.executable}\nimport json,sys,time\nfrom pathlib import Path\n"
        f"with Path({str(counter)!r}).open('a') as f: f.write('call\\n')\n"
        "time.sleep(0.1)\n"
        "out=Path(sys.argv[sys.argv.index('--output')+1])\n"
        "out.write_text(json.dumps({'ok':True,'http_status':200,'response':{'synthetic':True}}))\n"
    )
    binary.chmod(0o700)
    code = (
        "import sys;from pathlib import Path;from career_scout.decisions import DecisionsClient;"
        "c=DecisionsClient(Path(sys.argv[1]),workspace_root=Path(sys.argv[2]),binary=sys.argv[3]);"
        "assert c.request({'model':'synthetic'})['response']=={'synthetic':True}"
    )
    children = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(workspace), str(tmp_path), str(binary)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    try:
        for child in children:
            _, error = child.communicate(timeout=15)
            assert child.returncode == 0, error
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
    assert counter.read_text().splitlines() == ["call"]


@pytest.mark.parametrize(
    "schema,example",
    [
        ("worker-input", "worker-input-applications"),
        ("application-result", "application-pass"),
        ("evaluation-result", "evaluation-pass"),
    ],
)
def test_published_schemas_accept_generic_prepared_resume_identifiers(schema, example):
    root = Path(__file__).parents[1]
    schemas = [json.loads(p.read_text()) for p in (root / "schemas").glob("*.schema.json")]
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    definition = json.loads((root / "schemas" / (schema + ".schema.json")).read_text())
    value = json.loads((root / "examples" / (example + ".synthetic.json")).read_text())
    value["profile_id"] = "resume_main_7"
    if "resume" in value:
        value["resume"]["profile_id"] = "resume_main_7"
    Draft202012Validator(definition, registry=registry).validate(value)


def test_ambiguous_saved_native_receipt_is_not_permission_to_retry(tmp_path, monkeypatch):
    body = {"model": "synthetic"}
    receipt = tmp_path / (digest(body) + "-receipt.json")
    malformed = '{"ok":false,"ok":true,"http_status":200,"response":{}}'
    receipt.write_text(malformed)
    monkeypatch.setattr(decisions.subprocess, "run", lambda *a, **kw: pytest.fail("no retry"))
    client = decisions.DecisionsClient(tmp_path, workspace_root=tmp_path)
    with pytest.raises(EvaluationError, match="decisions_receipt_missing"):
        client.request(body)
    assert receipt.read_text() == malformed


@pytest.mark.parametrize("prior_status", [None, 503])
def test_timeout_without_receipt_stays_unknown_after_client_restart(
    tmp_path, monkeypatch, prior_status
):
    body = {"model": "synthetic"}
    if prior_status:
        (tmp_path / (digest(body) + "-receipt.json")).write_text(
            json.dumps({"ok": False, "http_status": prior_status})
        )
    monkeypatch.setattr(decisions.time, "sleep", lambda _: None)

    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired("synthetic", 1)

    monkeypatch.setattr(decisions.subprocess, "run", timeout)
    with pytest.raises(EvaluationError, match="decisions_outcome_unknown"):
        decisions.DecisionsClient(tmp_path, workspace_root=tmp_path, max_attempts=3).request(body)
    monkeypatch.setattr(decisions.subprocess, "run", lambda *a, **kw: pytest.fail("no repeat"))
    with pytest.raises(EvaluationError, match="decisions_outcome_unknown"):
        decisions.DecisionsClient(tmp_path, workspace_root=tmp_path, max_attempts=3).request(body)


def test_legacy_delivery_without_attempt_markers_requires_reconciliation(tmp_path, monkeypatch):
    body = {"model": "synthetic"}
    (tmp_path / (digest(body) + "-request.json")).write_text(json.dumps(body))
    (tmp_path / (digest(body) + "-receipt.json")).write_text(
        json.dumps({"ok": False, "http_status": 503})
    )
    monkeypatch.setattr(decisions.subprocess, "run", lambda *a, **kw: pytest.fail("no repeat"))
    with pytest.raises(EvaluationError, match="decisions_outcome_unknown"):
        decisions.DecisionsClient(tmp_path, workspace_root=tmp_path, max_attempts=3).request(body)
