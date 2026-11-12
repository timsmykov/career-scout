import copy
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from career_scout.analysis_graph import AnalysisGraph
from career_scout.analysis_store import AnalysisStore
from career_scout.codex_worker import (
    CodexWorker,
    WorkerReply,
    build_command,
    build_prompt,
    output_schema,
    worker_environment,
)
from career_scout.common import digest
from career_scout.evaluation import (
    ORDER,
    EvaluationError,
    EvaluationResult,
    EvaluatorConfig,
    ResumeSnapshot,
    make_input,
    text_hash,
    validate_result,
)
from career_scout.hh import normalize
from career_scout.models import Vacancy
from career_scout.normalization import project_snapshot


@pytest.fixture
def setup(tmp_path):
    raw = json.loads(Path("examples/vacancy-v2.synthetic.json").read_text())["source_payload"]
    raw["name"] = "Junior специалист по цифровым проектам"
    raw["description"] = "<p>Исследовать задачи заказчика и запускать цифровые сервисы.</p>"
    vacancy = Vacancy.model_validate(project_snapshot(normalize(raw, "1")))
    config = EvaluatorConfig(max_attempts_per_profile=2)
    requests = {}
    for profile in ORDER:
        text = "Исследовал задачи заказчика и запускал цифровые сервисы. " + profile
        resume = ResumeSnapshot(
            profile_id=profile,
            resume_id="synthetic-" + profile,
            source_url="https://example.org/resume/" + profile,
            title=profile,
            version="synthetic-1",
            captured_at=datetime(2026, 11, 12, tzinfo=UTC),
            content_hash=text_hash(text),
            text=text,
            provenance={"synthetic": True},
        )
        requests[profile] = make_input(
            vacancy=vacancy,
            resume=resume,
            filters={"salary_optional": True},
            filter_version="synthetic-1",
            rubric="Оцени соответствие роли " + profile,
            rubric_version="synthetic-1",
            common_instructions="Используй только переданное резюме.",
            config=config,
            evaluation_date=datetime(2026, 11, 12, tzinfo=UTC),
        )
    return tmp_path, config, requests


def result(request, verdict="PASS"):
    return {
        "schema_version": 2,
        "evaluation_key": request.evaluation_key,
        "profile_id": request.profile_id,
        "verdict": verdict,
        "reason": "Synthetic result.",
        "assessments": [
            {
                "kind": "responsibility",
                "requirement": "Основные обязанности",
                "mandatory": True,
                "vacancy_quote": "Исследовать задачи заказчика и запускать цифровые сервисы.",
                "resume_quote": "Исследовал задачи заказчика и запускал цифровые сервисы."
                if verdict == "PASS"
                else None,
                "status": {
                    "PASS": "confirmed",
                    "REJECT": "outside_profile",
                    "BORDERLINE": "unconfirmed",
                }[verdict],
                "explanation": "Synthetic evidence.",
            }
        ],
        "questions": ["Уточните обязательное условие."] if verdict == "BORDERLINE" else [],
    }


class ScriptedWorker:
    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.calls = []

    def __call__(self, request):
        self.calls.append(request.profile_id)
        value = self.verdicts[request.profile_id]
        if isinstance(value, BaseException):
            raise value
        return WorkerReply(result(request, value), {"input_tokens": 10, "output_tokens": 5})


def prepared(setup, *, max_calls=20, order=ORDER):
    path, config, requests = setup
    store = AnalysisStore(path / "analysis.sqlite3")
    rid = store.create_run({"model": config.model_dump(mode="json")}, max_calls)
    route = store.add_job(rid, [requests[p] for p in order])
    return store, rid, route


@pytest.mark.parametrize("accepted_index", range(4))
def test_first_pass_stops_later_workers(setup, accepted_index):
    _, config, _ = setup
    verdicts = dict.fromkeys(ORDER, "REJECT")
    verdicts[ORDER[accepted_index]] = "PASS"
    worker = ScriptedWorker(verdicts)
    store, rid, route = prepared(setup)
    outcome = AnalysisGraph(store, worker, config, order_confirmed=True).evaluate(rid, route)
    assert worker.calls == list(ORDER[: accepted_index + 1])
    assert outcome["status"] == "ACCEPTED"
    assert outcome["accepted_profile"] == ORDER[accepted_index]
    assert outcome["skipped"] == list(ORDER[accepted_index + 1 :])
    assert all(
        outcome["outcomes"][p]["verdict"] == "SKIPPED_AFTER_ACCEPT" for p in outcome["skipped"]
    )
    store.close()


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        (dict.fromkeys(ORDER, "REJECT"), "REJECTED"),
        ({**dict.fromkeys(ORDER, "REJECT"), "product": "BORDERLINE"}, "NEEDS_CLARIFICATION"),
        (
            {**dict.fromkeys(ORDER, "REJECT"), "product": EvaluationError("codex_timeout")},
            "PENDING",
        ),
        (
            {**dict.fromkeys(ORDER, "BORDERLINE"), "product": EvaluationError("codex_timeout")},
            "PENDING",
        ),
    ],
)
def test_aggregation_requires_four_valid_rejections(setup, verdicts, expected):
    _, config, _ = setup
    store, rid, route = prepared(setup)
    worker = ScriptedWorker(verdicts)
    outcome = AnalysisGraph(store, worker, config, order_confirmed=True).evaluate(rid, route)
    assert outcome["status"] == expected
    assert len(outcome["outcomes"]) == 4
    store.close()


def test_later_pass_wins_after_error_and_borderline(setup):
    _, config, _ = setup
    store, rid, route = prepared(setup)
    worker = ScriptedWorker(
        {
            "product": EvaluationError("codex_timeout"),
            "automation": "BORDERLINE",
            "business_assistant": "PASS",
        }
    )
    outcome = AnalysisGraph(store, worker, config, order_confirmed=True).evaluate(rid, route)
    assert outcome["status"] == "ACCEPTED"
    assert worker.calls == ["product", "product", "automation", "business_assistant"]
    assert outcome["outcomes"]["product"]["verdict"] == "ERROR"
    store.close()


def test_cache_is_reused_after_process_reopen(setup):
    path, config, requests = setup
    store, rid, route = prepared(setup)
    worker = ScriptedWorker(dict.fromkeys(ORDER, "REJECT"))
    first = AnalysisGraph(store, worker, config, order_confirmed=True).evaluate(rid, route)
    store.close()
    store = AnalysisStore(path / "analysis.sqlite3")
    new_run = store.create_run({}, 0)
    assert store.add_job(new_run, list(requests.values())) == route
    outcome = AnalysisGraph(store, worker, config, order_confirmed=True).evaluate(new_run, route)
    assert outcome["status"] == first["status"] == "REJECTED"
    assert all(o["cached"] for o in outcome["outcomes"].values())
    assert len(worker.calls) == 4 and store.run(new_run)["calls"] == 0
    store.close()


def test_changed_order_reuses_individual_results(setup):
    _, config, requests = setup
    store, rid, route = prepared(setup)
    worker = ScriptedWorker({"product": "REJECT", "automation": "PASS"})
    AnalysisGraph(store, worker, config, order_confirmed=True).evaluate(rid, route)
    order = ("automation", "product", "business_assistant", "business_analyst")
    second = store.create_run({}, 0)
    changed_route = store.add_job(second, [requests[p] for p in order])
    assert changed_route != route
    outcome = AnalysisGraph(store, worker, config, order=order, order_confirmed=True).evaluate(
        second, changed_route
    )
    assert outcome["accepted_profile"] == "automation" and len(worker.calls) == 2
    store.close()


def test_committed_result_survives_crash_before_checkpoint(setup, monkeypatch):
    path, config, _ = setup
    store, rid, route = prepared(setup)
    worker = ScriptedWorker({"product": "PASS"})
    save = store.save_result

    def crash(request, evaluated):
        save(request, evaluated)
        raise KeyboardInterrupt

    monkeypatch.setattr(store, "save_result", crash)
    with pytest.raises(KeyboardInterrupt):
        AnalysisGraph(store, worker, config, order_confirmed=True).evaluate(rid, route)
    store.close()
    store = AnalysisStore(path / "analysis.sqlite3")
    outcome = AnalysisGraph(store, worker, config, order_confirmed=True).evaluate(rid, route)
    assert outcome["status"] == "ACCEPTED" and worker.calls == ["product"]
    assert store.run(rid)["calls"] == 1
    store.close()


def test_budget_is_durable_and_explicit_extension_uses_cache(setup):
    path, config, _ = setup
    store, rid, route = prepared(setup, max_calls=1)
    worker = ScriptedWorker({"product": "REJECT", "automation": "PASS"})
    with pytest.raises(EvaluationError, match="analysis_call_budget_exhausted"):
        AnalysisGraph(store, worker, config, order_confirmed=True).evaluate(rid, route)
    store.close()
    store = AnalysisStore(path / "analysis.sqlite3")
    store.retry(rid, additional_calls=1)
    outcome = AnalysisGraph(store, worker, config, order_confirmed=True).evaluate(rid, route)
    assert outcome["accepted_profile"] == "automation"
    assert worker.calls == ["product", "automation"] and store.run(rid)["calls"] == 2
    store.close()


def test_fake_quote_becomes_error_never_rejection(setup):
    _, config, _ = setup
    store, rid, route = prepared(setup)

    def invalid(request):
        value = result(request, "REJECT")
        value["assessments"][0]["vacancy_quote"] = "This quote is absent."
        return WorkerReply(value)

    outcome = AnalysisGraph(store, invalid, config, order_confirmed=True).evaluate(rid, route)
    assert outcome["status"] == "PENDING"
    assert all(o["verdict"] == "ERROR" for o in outcome["outcomes"].values())
    assert store.db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 0
    assert store.run(rid)["calls"] == 8
    store.close()


def test_fatal_subscription_error_stops_batch_without_false_reject(setup):
    _, config, _ = setup
    store, rid, route = prepared(setup)
    worker = ScriptedWorker({"product": EvaluationError("codex_rate_limited", fatal=True)})
    with pytest.raises(EvaluationError, match="codex_rate_limited"):
        AnalysisGraph(store, worker, config, order_confirmed=True).evaluate(rid, route)
    assert worker.calls == ["product"]
    assert store.db.execute("SELECT status FROM run_jobs").fetchone()[0] == "PENDING"
    store.close()


def test_optional_gap_can_pass_but_mandatory_gap_cannot(setup):
    request = setup[2]["product"]
    value = result(request)
    optional = copy.deepcopy(value["assessments"][0])
    optional.update(kind="requirement", mandatory=False, status="unconfirmed", resume_quote=None)
    value["assessments"].append(optional)
    validate_result(request, EvaluationResult.model_validate(value))
    value["assessments"][-1]["mandatory"] = True
    with pytest.raises(ValidationError, match="pass_with_unresolved_requirement"):
        EvaluationResult.model_validate(value)


def test_unknown_experience_cannot_be_used_as_reject(setup):
    value = result(setup[2]["product"], "REJECT")
    value["assessments"][0]["status"] = "unconfirmed"
    with pytest.raises(ValidationError, match="reject_without_mandatory"):
        EvaluationResult.model_validate(value)


def test_hashes_and_exact_quotes_are_enforced(setup):
    request = setup[2]["product"]
    changed = request.model_dump(mode="json")
    changed["resume"]["text"] += " invented skill"
    with pytest.raises(ValidationError):
        type(request).model_validate(changed)
    value = result(request)
    value["assessments"][0]["resume_quote"] = value["assessments"][0]["resume_quote"].upper()
    with pytest.raises(EvaluationError, match="resume_quote_not_found"):
        validate_result(request, EvaluationResult.model_validate(value))


def test_each_prompt_contains_only_its_own_resume(setup):
    for profile, request in setup[2].items():
        prompt = build_prompt(request)
        assert request.resume.text in prompt
        assert all(other.resume.text not in prompt for p, other in setup[2].items() if p != profile)


def test_codex_worker_has_no_api_keys_or_shared_context(monkeypatch, tmp_path, setup):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-pass")
    monkeypatch.setenv("CODEX_API_KEY", "must-not-pass")
    monkeypatch.setenv("CODEX_THREAD_ID", "parent-context-must-not-pass")
    assert all(
        key not in worker_environment()
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_THREAD_ID")
    )
    argv = build_command(setup[1], tmp_path)
    assert "--ignore-user-config" in argv and "--ephemeral" in argv
    assert "project_doc_max_bytes=0" in argv
    assert "memories" in argv and "multi_agent" in argv and "shell_tool" in argv
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv


def test_oversized_input_is_not_truncated_or_sent(setup):
    request = setup[2]["product"]
    config = EvaluatorConfig(max_input_characters=100)
    changed = request.model_dump(mode="json")
    changed["model_config_hash"] = config.key()
    changed["evaluation_key"] = digest(
        {k: v for k, v in changed.items() if k not in {"evaluation_key", "evaluation_date"}}
    )
    request = type(request).model_validate(changed)
    with pytest.raises(EvaluationError, match="input_exceeds_configured_context"):
        CodexWorker(config)(request)


def test_collector_database_is_refused_without_changes(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE vacancies(id TEXT PRIMARY KEY,payload TEXT)")
    db.execute("INSERT INTO vacancies VALUES('original','preserved')")
    db.commit()
    db.close()
    before = path.read_bytes()
    with pytest.raises(EvaluationError, match="not_an_analysis_database"):
        AnalysisStore(path)
    assert path.read_bytes() == before


def test_unconfirmed_order_cannot_start_workers(setup):
    store, _, _ = prepared(setup)
    with pytest.raises(EvaluationError, match="profile_order_not_confirmed"):
        AnalysisGraph(store, ScriptedWorker({}), setup[1])
    store.close()


def configured_request(request, config):
    values = request.model_dump(mode="json")
    values["model_config_hash"] = config.key()
    values["evaluation_key"] = digest(
        {k: v for k, v in values.items() if k not in {"evaluation_key", "evaluation_date"}}
    )
    return type(request).model_validate(values)


def fake_codex(tmp_path, body, *, auth="Logged in using ChatGPT"):
    path = tmp_path / "fake-codex"
    path.write_text(
        f"#!{sys.executable}\nimport json,sys,os\nfrom pathlib import Path\n"
        f"if sys.argv[1:3]==['login','status']:\n print({auth!r});sys.exit(0)\n"
        "prompt=sys.stdin.read()\n" + body
    )
    path.chmod(0o700)
    return str(path)


def test_subscription_transport_and_usage_without_api_key(setup):
    path, config, requests = setup
    payload = result(requests["product"])
    binary = fake_codex(
        path,
        "target=Path(sys.argv[sys.argv.index('--output-last-message')+1])\n"
        f"target.write_text(json.dumps({payload!r}))\n"
        "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':42,'output_tokens':7}}))\n",
    )
    reply = CodexWorker(config, binary=binary)(requests["product"])
    assert reply.result == payload
    assert reply.usage == {"input_tokens": 42, "output_tokens": 7}


def test_api_login_is_refused_before_worker_starts(setup):
    path, config, requests = setup
    sentinel = path / "executed"
    binary = fake_codex(
        path, f"Path({str(sentinel)!r}).touch()\n", auth="Logged in using an API key"
    )
    with pytest.raises(EvaluationError, match="codex_chatgpt_login_required"):
        CodexWorker(config, binary=binary)(requests["product"])
    assert not sentinel.exists()


def test_worker_rejects_tool_call_event(setup):
    path, config, requests = setup
    binary = fake_codex(
        path, "print(json.dumps({'type':'item.completed','item':{'type':'mcp_tool_call'}}))\n"
    )
    with pytest.raises(EvaluationError, match="worker_tool_use_not_allowed") as error:
        CodexWorker(config, binary=binary)(requests["product"])
    assert error.value.fatal


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("usage limit reached", "codex_rate_limited"),
        ("authentication token_expired", "codex_auth_required"),
        ("unsupported model", "codex_model_unavailable"),
    ],
)
def test_subscription_failures_are_safe_and_fatal(setup, message, expected):
    path, config, requests = setup
    binary = fake_codex(
        path, f"print(json.dumps({{'type':'error','message':{message!r}}}));sys.exit(1)\n"
    )
    with pytest.raises(EvaluationError, match=expected) as error:
        CodexWorker(config, binary=binary)(requests["product"])
    assert error.value.fatal and str(error.value) == expected


def test_worker_timeout_stops_its_own_process_group(setup):
    path, _, requests = setup
    config = EvaluatorConfig(timeout_seconds=1)
    request = configured_request(requests["product"], config)
    child_file = path / "child-pid"
    binary = fake_codex(
        path,
        "import subprocess,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
        f"Path({str(child_file)!r}).write_text(str(child.pid))\n"
        "time.sleep(30)\n",
    )
    with pytest.raises(EvaluationError, match="codex_timeout"):
        CodexWorker(config, binary=binary)(request)
    pid = int(child_file.read_text())
    for _ in range(20):
        stat = Path(f"/proc/{pid}/stat")
        if not stat.exists() or stat.read_text().split()[2] == "Z":
            break
        time.sleep(0.05)
    else:
        pytest.fail("A worker child was left running after timeout")


def test_result_writes_and_background_checkpoints_use_separate_transactions(setup):
    _, config, templates = setup
    store, _, _ = prepared(setup)
    worker = ScriptedWorker(dict.fromkeys(ORDER, "REJECT"))
    for index in range(10):
        requests = []
        for template in templates.values():
            data = template.model_dump(mode="json")
            data["rubric_version"] = f"checkpoint-stress-{index}"
            data["evaluation_key"] = digest(
                {k: v for k, v in data.items() if k not in {"evaluation_key", "evaluation_date"}}
            )
            requests.append(type(template).model_validate(data))
        rid = store.create_run({}, 4)
        route = store.add_job(rid, requests)
        outcome = AnalysisGraph(store, worker, config, order_confirmed=True).evaluate(rid, route)
        assert outcome["status"] == "REJECTED" and store.run(rid)["calls"] == 4
    assert len(worker.calls) == 40
    assert not store.db.execute("PRAGMA foreign_key_check").fetchall()
    assert store.db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    store.close()


def test_outside_primary_responsibility_can_reject_without_mandatory_label(setup):
    value = result(setup[2]["product"], "REJECT")
    value["assessments"][0]["mandatory"] = None
    validate_result(setup[2]["product"], EvaluationResult.model_validate(value))
    value["assessments"][0].update(kind="requirement", mandatory=False)
    with pytest.raises(ValidationError, match="reject_without_mandatory"):
        EvaluationResult.model_validate(value)


def test_wire_schema_quotes_are_contiguous_original_spans(setup):
    request = setup[2]["product"]
    schema = output_schema(request)
    fields = schema["$defs"]["Assessment"]["properties"]
    from career_scout.evaluation import canonical_quote, vacancy_evidence_text

    for quote in fields["vacancy_quote"]["enum"]:
        assert canonical_quote(quote) in canonical_quote(vacancy_evidence_text(request.vacancy))
    for quote in fields["resume_quote"]["anyOf"][0]["enum"]:
        assert canonical_quote(quote) in canonical_quote(request.resume.text)
    assert "invented or stitched citation" not in fields["vacancy_quote"]["enum"]
