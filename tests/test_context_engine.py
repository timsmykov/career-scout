"""Personalized flow acceptance without network/model charges or production data."""

import json
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_agent_cli import invoke
from test_analysis import fake_codex

from career_scout import agent_service, context_engine
from career_scout.agent_models import ContextInput
from career_scout.agent_state import AgentState
from career_scout.analysis_store import AnalysisStore
from career_scout.batch_worker import BatchReply, CodexBatchWorker
from career_scout.codex_worker import WorkerReply
from career_scout.context_commands import metrics
from career_scout.context_models import EvaluationConnection, FactRule
from career_scout.context_policy import ContextResult
from career_scout.evaluation import EvaluationError, validate_result
from career_scout.hh import normalize
from career_scout.models import Vacancy
from career_scout.normalization import project_snapshot


def vacancy(i=1, salary=None):
    return Vacancy.model_validate(
        project_snapshot(
            normalize(
                {
                    "id": str(i),
                    "name": "Врач-кардиолог",
                    "description": "<p>Лечить пациентов. Работа удалённо.</p>",
                    "published_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                    **({"salary_range": salary} if salary is not None else {}),
                },
                str(i),
            )
        )
    )


def document(n=1, prefs=None):
    return {
        "resumes": [
            {
                "id": f"cv{i}",
                "title": f"Кардиолог {i}",
                "text": "Лечил пациентов. Проводил консультации.",
            }
            for i in range(n)
        ],
        "goals": "Клиническая работа",
        "preferences": prefs or [],
    }


def prepared(tmp_path, n=1, count=2, max_calls=4, max_decisions=20, prefs=None):
    state = AgentState(tmp_path / "ws", write=True)
    record = agent_service.context_create(state, document(n, prefs), "context")["result"]
    config = EvaluationConnection(external_calls_enabled=True, batch_size=20)
    result = context_engine.prepare(
        state,
        record["context_id"],
        None,
        [vacancy(i).model_dump(mode="json") for i in range(count)],
        config.model_dump(mode="json"),
        100,
        max_calls,
        max_decisions,
    )
    return state, result, config


def answer(request, verdict="PASS"):
    status = (
        "outside_profile"
        if verdict == "REJECT"
        else "unconfirmed"
        if verdict == "UNCERTAIN"
        else "confirmed"
    )
    return ContextResult.model_validate(
        {
            "schema_version": 4,
            "policy": "context-1",
            "evaluation_key": request.evaluation_key,
            "profile_id": request.profile_id,
            "verdict": verdict,
            "reason": "Основание по исходникам",
            "assessments": [
                {
                    "kind": "responsibility",
                    "requirement": "Лечить пациентов",
                    "mandatory": True,
                    "critical": True,
                    "vacancy_quote": "Лечить пациентов.",
                    "resume_quote": "Лечил пациентов." if status == "confirmed" else None,
                    "status": status,
                    "explanation": "Сопоставление основной работы",
                }
            ],
            "preference_checks": [
                {
                    "preference_id": p["id"],
                    "status": "met",
                    "vacancy_quote": "Работа удалённо.",
                    "reason": "Условие соблюдено",
                }
                for p in request.filters["candidate_preferences"]
            ],
            "uncertainties": ["Недостаточно данных об основной работе"]
            if verdict == "UNCERTAIN"
            else [],
            "questions": [],
        }
    )


class Worker:
    calls = []
    verdict = "PASS"

    def __init__(self, config):
        pass

    def cancel(self):
        pass

    def batch(self, requests):
        self.calls.append([r.evaluation_key for r in requests])
        return BatchReply(
            {
                r.evaluation_key: WorkerReply(answer(r, self.verdict).model_dump(mode="json"))
                for r in requests
            },
            {},
            {"input_tokens": 100, "output_tokens": 20},
            {},
        )


@pytest.fixture(autouse=True)
def reset_worker():
    Worker.calls = []
    Worker.verdict = "PASS"


def test_single_resume_context_is_evaluated_without_classification_and_cached(tmp_path):
    state, p, _ = prepared(tmp_path, count=21)
    with closing(AnalysisStore(Path(p["database"]))) as store:
        assert context_engine.execute(store, p["run_id"], worker_factory=Worker) is None
        assert sorted(map(len, Worker.calls)) == [1, 20]
        assert context_engine.execute(store, p["run_id"], worker_factory=Worker) is None
        assert len(Worker.calls) == 2
    result = context_engine.status(Path(p["database"]), p["run_id"])
    assert result["status"] == "complete" and result["outcomes"] == {"ACCEPTED": 21}
    assert result["decisions_requests_reserved"] == 0
    rows = context_engine.recommendations(p["database"])
    assert len(rows) == 21 and rows[0]["category"] == "Кардиолог 0"
    assert rows[0]["verdict"] == "PASS"
    state.close()


def test_admission_advances_across_prepares_and_does_not_revisit_rejections(tmp_path):
    state, p, c = prepared(tmp_path, count=2)
    Worker.verdict = "REJECT"
    with closing(AnalysisStore(Path(p["database"]))) as store:
        context_engine.execute(store, p["run_id"], worker_factory=Worker)
    more = context_engine.prepare(
        state,
        p["context_id"]
        if "context_id" in p
        else state.db.execute("SELECT id FROM contexts").fetchone()[0],
        None,
        [vacancy(i).model_dump(mode="json") for i in range(3)],
        c.model_dump(mode="json"),
        100,
        4,
        20,
    )
    assert more["admitted"] == 1 and more["already_admitted"] == 2
    assert context_engine.recommendations(p["database"]) == []
    state.close()


def test_uncertainty_completes_without_questions_and_is_separate_from_reject(tmp_path):
    state, p, _ = prepared(tmp_path, count=1)
    Worker.verdict = "UNCERTAIN"
    with closing(AnalysisStore(Path(p["database"]))) as store:
        context_engine.execute(store, p["run_id"], worker_factory=Worker)
    assert context_engine.status(p["database"], p["run_id"])["status"] == "complete"
    assert context_engine.recommendations(p["database"]) == []
    rows = context_engine.recommendations(p["database"], include_uncertain=True)
    assert len(rows) == 1 and rows[0]["verdict"] == "UNCERTAIN" and rows[0]["uncertainties"]
    state.close()


@pytest.mark.parametrize("n", [1, 2, 7])
def test_native_choices_come_from_resume_ids_and_ambiguity_selects_two(tmp_path, n):
    state, p, c = prepared(tmp_path, n=n, count=1)
    context = state.context(state.db.execute("SELECT id FROM contexts").fetchone()[0])
    body = context_engine.decision_body(context, vacancy())
    choices = body["questions"][0]["choices"]
    assert {x["value"] for x in choices} == {
        *(f"cv{i}" for i in range(n)),
        "@uncertain",
        "@mismatch",
    }
    probabilities = {x["value"]: 0.0 for x in choices}
    probabilities["cv0"] = 0.51 if n > 1 else 1.0
    if n > 1:
        probabilities["cv1"] = 0.49
    payload = {
        "model": "gpt-6-luna",
        "answers": [
            {
                "type": "choice",
                "name": "resume",
                "choice": "cv0",
                "probabilities": [{"value": k, "probability": v} for k, v in probabilities.items()],
            }
        ],
    }
    route = context_engine.parse_route(payload, [f"cv{i}" for i in range(n)], c)
    assert (
        route["selected_profiles"] == ["cv0", "cv1"]
        if n > 1
        else route["selected_profiles"] == ["cv0"]
    )

    class Client:
        calls = 0

        def request(self, request):
            self.calls += 1
            return {"response": payload}

    client = Client()
    with closing(AnalysisStore(Path(p["database"]))) as store:
        context_engine.execute(store, p["run_id"], client=client, worker_factory=Worker)
    assert client.calls == (0 if n == 1 else 1)
    state.close()


def test_decisions_mismatch_is_not_an_automatic_rejection_by_default():
    payload = {
        "model": "gpt-6-luna",
        "answers": [
            {
                "type": "choice",
                "name": "resume",
                "choice": "@mismatch",
                "probabilities": [
                    {"value": "cv", "probability": 0.0},
                    {"value": "@uncertain", "probability": 0.0},
                    {"value": "@mismatch", "probability": 1.0},
                ],
            }
        ],
    }
    assert not context_engine.parse_route(payload, ["cv"], EvaluationConnection())["exclusion"]
    assert context_engine.parse_route(
        payload, ["cv"], EvaluationConnection(allow_decisions_exclusion=True)
    )["exclusion"]
    payload["answers"][0]["probabilities"][0]["probability"] = float("nan")
    with pytest.raises(EvaluationError):
        context_engine.parse_route(payload, ["cv"], EvaluationConnection())


def test_fact_rules_keep_missing_currency_or_period_and_check_numeric_upper_bound():
    rule = FactRule(
        id="salary",
        field="attributes.salary.amount_to",
        operator="gte",
        value=80000,
        guards={"attributes.salary.currency": "RUB", "attributes.salary.period": "month"},
    )
    ctx = ContextInput.model_validate({**document(), "fact_rules": [rule.model_dump()]})
    assert context_engine.rule_checks(ctx, vacancy())[0]["status"] == "unknown"
    low = vacancy(salary={"from": 50000, "to": 70000, "currency": "RUR", "mode": {"id": "MONTH"}})
    assert context_engine.rule_checks(ctx, low)[0]["status"] == "violated"
    unknown = vacancy(salary={"from": 50000, "to": 70000, "currency": "RUR"})
    assert context_engine.rule_checks(ctx, unknown)[0]["status"] == "unknown"
    with pytest.raises(ValidationError):
        FactRule(id="bad", field="attributes.salary.amount_to", operator="gte", value=80000)


def test_preference_coverage_evidence_and_missing_skill_are_validated(tmp_path):
    state, p, c = prepared(
        tmp_path, count=1, prefs=[{"statement": "Только удалённо", "priority": "required"}]
    )
    record = state.context(state.db.execute("SELECT id FROM contexts").fetchone()[0])
    request = context_engine.inputs(record, vacancy(), c)["cv0"]
    valid = answer(request)
    assert validate_result(request, valid) == valid
    invalid = valid.model_dump(mode="json")
    invalid["preference_checks"] = []
    with pytest.raises(EvaluationError, match="preference_coverage_mismatch"):
        validate_result(request, ContextResult.model_validate(invalid))
    invalid = valid.model_dump(mode="json")
    invalid["preference_checks"][0].update(status="unknown", vacancy_quote=None)
    with pytest.raises(EvaluationError, match="pass_with_unresolved_preference"):
        validate_result(request, ContextResult.model_validate(invalid))
    missing = answer(request, "UNCERTAIN").model_dump(mode="json")
    missing["verdict"] = "REJECT"
    with pytest.raises(EvaluationError, match="reject_without_proven_blocker"):
        validate_result(request, ContextResult.model_validate(missing))
    state.close()


def test_native_batch_transport_supports_context_schema_and_preference_anchors(tmp_path):
    state, p, c = prepared(tmp_path, count=1, prefs=["Удалённо"])
    request = context_engine.inputs(
        state.context(state.db.execute("SELECT id FROM contexts").fetchone()[0]), vacancy(), c
    )["cv0"]
    from career_scout.evidence import documents

    vacancy_doc, resume_doc = documents(request, "B0V")
    result = answer(request).model_dump(mode="json")
    a = result["assessments"][0]
    a.pop("vacancy_quote")
    a.pop("resume_quote")
    a.update(
        vacancy_reference={"from": vacancy_doc.ids[0], "to": vacancy_doc.ids[0]},
        resume_reference={"from": resume_doc.ids[0], "to": resume_doc.ids[0]},
    )
    pref = result["preference_checks"][0]
    pref.pop("vacancy_quote")
    pref["vacancy_reference"] = {"from": vacancy_doc.ids[0], "to": vacancy_doc.ids[0]}
    wire = {"results": [result]}
    binary = fake_codex(
        tmp_path,
        "target=Path(sys.argv[sys.argv.index('--output-last-message')+1])\n"
        + f"target.write_text(json.dumps({wire!r}))\n",
    )
    cfg = c.model.model_copy(update={"executable": binary})
    request = context_engine.inputs(
        state.context(state.db.execute("SELECT id FROM contexts").fetchone()[0]),
        vacancy(),
        c.model_copy(update={"model": cfg}),
    )["cv0"]
    # Evaluation identity changes with executor; update the synthetic wire key accordingly.
    content = Path(binary).read_text().replace(result["evaluation_key"], request.evaluation_key)
    Path(binary).write_text(content)
    reply = CodexBatchWorker(cfg).batch([request])
    assert not reply.errors and request.evaluation_key in reply.replies
    state.close()


def test_cli_connection_doctor_prepare_run_disabled_and_schema(tmp_path, capsys):
    ctx = invoke(tmp_path, capsys, "context", "create", "--request-id", "c", data=document())[
        "result"
    ]["context_id"]
    connection = invoke(
        tmp_path, capsys, "connection", "put", "default", "--request-id", "conn", data={}
    )
    assert connection["status"] == "ok"
    diagnostic = invoke(tmp_path, capsys, "doctor")["result"]
    assert diagnostic["network_calls"] == 0 and not diagnostic["auth_live_verified"]
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([vacancy().model_dump(mode="json")]))
    prepared = invoke(
        tmp_path,
        capsys,
        "evaluation",
        "prepare",
        "--context",
        ctx,
        "--catalog",
        str(catalog),
        "--max-calls",
        "0",
        "--max-decisions",
        "0",
        "--request-id",
        "ep",
    )
    assert prepared["status"] == "ok", prepared
    p = prepared["result"]
    run = invoke(
        tmp_path,
        capsys,
        "evaluation",
        "run",
        p["run_id"],
        "--db",
        p["database"],
        "--request-id",
        "er",
    )
    assert run["error"]["code"] == "model_calls_disabled"
    shown = invoke(tmp_path, capsys, "evaluation", "status", p["run_id"], "--db", p["database"])
    assert shown["result"]["catalog"] == {"PENDING": 1}
    assert invoke(tmp_path, capsys, "schema")["result"]["capabilities"][
        "generic_context_evaluation"
    ]


def test_quality_report_uses_context_bound_labels_and_never_claims_unmeasured_accuracy(tmp_path):
    state, p, _ = prepared(tmp_path, count=1)
    with closing(AnalysisStore(Path(p["database"]))) as store:
        context_engine.execute(store, p["run_id"], worker_factory=Worker)
    assert not metrics(p["database"], p["run_id"])["quality"]["measured"]
    labels = tmp_path / "labels.json"
    context = state.context(state.db.execute("SELECT id FROM contexts").fetchone()[0])
    labels.write_text(
        json.dumps(
            {
                "context_hash": context["content_hash"],
                "cases": [{"source": "hh", "source_id": "0", "expected": "PASS"}],
            }
        )
    )
    quality = metrics(p["database"], p["run_id"], labels)["quality"]
    assert quality["precision"] == 1 and quality["recall"] == 1 and quality["labeled_count"] == 1
    assert quality["scope"] == "supplied_labeled_set_only"
    state.close()


def test_history_is_idempotent_and_hides_applied_across_context_versions(tmp_path):
    from career_scout.context_commands import history_import, history_list

    state, p, _ = prepared(tmp_path, count=1)
    with closing(AnalysisStore(Path(p["database"]))) as store:
        context_engine.execute(store, p["run_id"], worker_factory=Worker)
    assert len(context_engine.recommendations(p["database"])) == 1
    event = {
        "operation_id": "already-applied",
        "source": "hh",
        "source_id": "0",
        "kind": "applied",
        "evidence": "synthetic owner import",
        "detail": {},
    }
    a = history_import(state, [event], "import")
    assert history_import(state, [event], "import") == a
    assert history_list(state)["total"] == 1
    assert context_engine.recommendations(p["database"]) == []
    state.close()


def test_exhausted_decisions_budget_keeps_routed_results_and_resume_adds_only_remainder(tmp_path):
    state, p, c = prepared(tmp_path, n=2, count=2, max_decisions=1)

    class Client:
        calls = 0

        def request(self, body):
            self.calls += 1
            return {
                "response": {
                    "model": "gpt-6-luna",
                    "answers": [
                        {
                            "type": "choice",
                            "name": "resume",
                            "choice": "cv0",
                            "probabilities": [
                                {"value": "cv0", "probability": 1.0},
                                {"value": "cv1", "probability": 0.0},
                                {"value": "@mismatch", "probability": 0.0},
                                {"value": "@uncertain", "probability": 0.0},
                            ],
                        }
                    ],
                }
            }

    client = Client()
    with closing(AnalysisStore(Path(p["database"]))) as store:
        assert (
            context_engine.execute(store, p["run_id"], client=client, worker_factory=Worker)
            == "routing_request_budget_exhausted"
        )
        assert len(Worker.calls) == 1 and client.calls == 1
        assert (
            context_engine.execute(
                store, p["run_id"], client=client, worker_factory=Worker, additional_decisions=1
            )
            is None
        )
        assert len(Worker.calls) == 2 and client.calls == 2
    assert context_engine.status(p["database"], p["run_id"])["status"] == "complete"
    state.close()


def test_unknown_worker_failure_does_not_repeat_a_model_operation(tmp_path):
    state, p, _ = prepared(tmp_path, count=1)

    class Broken(Worker):
        def batch(self, requests):
            raise RuntimeError("do not expose this")

    with closing(AnalysisStore(Path(p["database"]))) as store:
        assert (
            context_engine.execute(store, p["run_id"], worker_factory=Broken)
            == "worker_execution_error"
        )
        with pytest.raises(EvaluationError, match="batch_outcome_unknown"):
            context_engine.execute(store, p["run_id"], worker_factory=Worker)
    assert not Worker.calls
    state.close()


def test_unknown_publication_is_kept_and_stale_jobs_do_not_reach_models(tmp_path):
    state, p, c = prepared(tmp_path, count=0)
    old = vacancy(99).model_dump(mode="json")
    old["published_at"] = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    cid = state.db.execute("SELECT id FROM contexts").fetchone()[0]
    new = context_engine.prepare(state, cid, None, [old], c.model_dump(mode="json"), 100, 4, 20)
    with closing(AnalysisStore(Path(new["database"]))) as store:
        context_engine.execute(store, new["run_id"], worker_factory=Worker)
    assert not Worker.calls
    assert context_engine.status(new["database"], new["run_id"])["catalog"] == {"STALE": 1}
    missing = vacancy(100).model_copy(update={"published_at": None})
    assert context_engine.fresh(missing, 14) is None
    state.close()


def test_context_wire_schema_enforces_status_and_resume_evidence_together(tmp_path):
    from career_scout.codex_worker import output_schema

    state, p, c = prepared(tmp_path, count=1)
    request = context_engine.inputs(
        state.context(state.db.execute("SELECT id FROM contexts").fetchone()[0]), vacancy(), c
    )["cv0"]
    variants = output_schema(request)["$defs"]["ApplicationAssessment"]["anyOf"]
    assert variants[0]["properties"]["resume_reference"]["type"] == "object"
    assert variants[1]["properties"]["resume_reference"] == {"type": "null"}
    assert variants[1]["properties"]["status"]["enum"] == ["unconfirmed", "outside_profile"]
    state.close()


@pytest.mark.parametrize("field", ["external_calls_enabled", "allow_decisions_exclusion"])
def test_enabling_calls_or_automatic_exclusion_requires_literal_boolean(field):
    with pytest.raises(ValidationError):
        EvaluationConnection.model_validate({field: "yes"})


def test_quality_labels_reject_duplicate_or_malformed_inputs():
    from career_scout.context_models import QualityLabels

    item = {"source": "hh", "source_id": "1", "expected": "PASS"}
    with pytest.raises(ValidationError):
        QualityLabels(context_hash="0" * 64, cases=[item, item])
    with pytest.raises(ValidationError):
        QualityLabels.model_validate([])


def test_history_import_between_prepare_and_execute_prevents_model_work(tmp_path):
    from career_scout.context_commands import history_import

    state, p, _ = prepared(tmp_path, count=1)
    event = {
        "operation_id": "applied",
        "source": "hh",
        "source_id": "0",
        "kind": "applied",
        "evidence": "synthetic",
        "detail": {},
    }
    history_import(state, [event], "history")
    with closing(AnalysisStore(Path(p["database"]))) as store:
        context_engine.execute(store, p["run_id"], worker_factory=Worker)
    assert not Worker.calls
    assert context_engine.status(p["database"], p["run_id"])["catalog"] == {"OWNER_EXCLUDED": 1}
    history_import(state, [{**event, "operation_id": "reopen", "kind": "reopened"}], "reopen")
    with closing(AnalysisStore(Path(p["database"]))) as store:
        context_engine.execute(store, p["run_id"], worker_factory=Worker)
    assert len(Worker.calls) == 1
    state.close()


def test_existing_context_without_new_default_fields_keeps_noop_version_and_receipt(tmp_path):
    state = AgentState(tmp_path / "ws", write=True)
    old = ContextInput.model_validate(document()).model_dump(mode="json")
    old.pop("fact_rules")
    cid = "ctx_legacy"
    from career_scout.common import digest, utcnow

    now = utcnow()
    state.db.execute("INSERT INTO contexts VALUES(?,1,?)", (cid, now))
    state.db.execute(
        "INSERT INTO context_versions VALUES(?,1,?,?,?)", (cid, json.dumps(old), digest(old), now)
    )
    receipt = {
        "schema_version": 1,
        "status": "ok",
        "result": {"context_id": cid},
        "operation_id": "original",
    }
    state.begin("original", "context.create", old)
    state.finish("original", receipt)
    state.db.commit()
    assert agent_service.context_create(state, document(), "original") == receipt
    update = agent_service.context_update(state, cid, {}, 1, "noop")
    assert update["result"]["version"] == 1 and update["result"]["unchanged"]
    assert "fact_rules" not in state.context(cid)["document"]
    state.close()


def test_prepared_offline_run_can_be_explicitly_enabled_without_readmitting_inputs(tmp_path):
    from career_scout.agent_cli import parser

    state, p, _ = prepared(tmp_path, count=1)
    parsed = parser().parse_args(
        [
            "evaluation",
            "resume",
            p["run_id"],
            "--db",
            p["database"],
            "--enable-calls",
            "--request-id",
            "enable",
        ]
    )
    assert parsed.enable_calls
    with closing(AnalysisStore(Path(p["database"]))) as store:
        frozen = json.loads(store.run(p["run_id"])["config_json"])
        frozen["connection"]["external_calls_enabled"] = False
        store.db.execute(
            "UPDATE analysis_runs SET config_json=? WHERE id=?", (json.dumps(frozen), p["run_id"])
        )
        store.db.commit()
        with pytest.raises(EvaluationError, match="model_calls_disabled"):
            context_engine.execute(store, p["run_id"], worker_factory=Worker)
        assert (
            context_engine.execute(store, p["run_id"], worker_factory=Worker, enable_calls=True)
            is None
        )
        assert len(Worker.calls) == 1
    assert context_engine.status(p["database"], p["run_id"])["external_calls_enabled"]
    state.close()
