"""Application policy and durable intake do not alter legacy evaluation history."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_analysis import fake_codex, result
from test_analysis import setup as base_setup
from test_routed_analysis import batch_wire, clone

from career_scout import analysis, intake
from career_scout.analysis_store import AnalysisStore
from career_scout.application import ApplicationResult, parse_result
from career_scout.batch_worker import CodexBatchWorker
from career_scout.common import digest
from career_scout.evaluation import ORDER, make_input
from career_scout.shortlist import recommendations, render, render_grouped


@pytest.fixture
def fixture(tmp_path):
    return base_setup.__wrapped__(tmp_path)


def app_input(request, config, published=None):
    return make_input(
        vacancy=request.vacancy.model_copy(update={"published_at": published})
        if published
        else request.vacancy,
        resume=request.resume,
        filters={**request.filters, "evaluation_policy": "applications-1"},
        filter_version=request.filter_version,
        rubric=request.rubric,
        rubric_version="applications-1",
        common_instructions=Path("prompts/applications.md").read_text(),
        config=config,
        evaluation_date=request.evaluation_date,
    )


def app_result(request):
    value = result(request)
    value.update(schema_version=3, policy="applications-1")
    for a in value["assessments"]:
        a["critical"] = True
    return value


def test_noncritical_unknown_is_allowed_without_inventing_experience_or_questions(fixture):
    request = app_input(fixture[2]["product"], fixture[1])
    value = app_result(request)
    value["assessments"].append(
        {
            **value["assessments"][0],
            "kind": "requirement",
            "critical": False,
            "mandatory": True,
            "status": "unconfirmed",
            "resume_quote": None,
            "requirement": "Базовые презентации",
        }
    )
    assert ApplicationResult.model_validate(value).verdict == "PASS"
    value["assessments"][-1]["critical"] = True
    with pytest.raises(ValidationError, match="recommendation_with_critical_gap"):
        ApplicationResult.model_validate(value)
    value["verdict"] = "REJECT"
    assert ApplicationResult.model_validate(value).verdict == "REJECT"
    value["questions"] = ["Что-то уточните"]
    with pytest.raises(ValidationError):
        ApplicationResult.model_validate(value)


def test_core_evidence_and_rejection_reason_are_required(fixture):
    request = app_input(fixture[2]["product"], fixture[1])
    value = app_result(request)
    value["assessments"][0]["critical"] = False
    with pytest.raises(ValidationError, match="recommendation_without_core_evidence"):
        ApplicationResult.model_validate(value)
    value["verdict"] = "REJECT"
    with pytest.raises(ValidationError, match="skip_without_critical_reason"):
        ApplicationResult.model_validate(value)


def test_native_application_batch_schema_and_literal_evidence(fixture):
    path, config, profiles = fixture
    requests = [app_input(clone(profiles["product"], i), config) for i in range(2)]
    wire = batch_wire(requests)
    for row in wire["results"]:
        row.update(schema_version=3, policy="applications-1")
        for a in row["assessments"]:
            a["critical"] = True
    binary = fake_codex(
        path,
        "target=Path(sys.argv[sys.argv.index('--output-last-message')+1])\n"
        f"target.write_text(json.dumps({wire!r}))\n",
    )
    reply = CodexBatchWorker(config, binary=binary).batch(requests)
    assert len(reply.replies) == 2 and not reply.errors
    store = AnalysisStore(path / "store.sqlite3")
    for request in requests:
        store.save_input(request)
        store.save_result(request, parse_result(reply.replies[request.evaluation_key].result))
        assert store.cached(request).schema_version == 3
    store.close()


def setup_config(fixture, monkeypatch):
    path, config, profiles = fixture
    cfg = json.loads(Path("configs/analysis.routed.example.json").read_text())
    for p in ORDER:
        target = path / (p + ".json")
        target.write_text(profiles[p].resume.model_dump_json())
        cfg["profiles"][p] = str(target)
        cfg["rubrics"][p] = str(Path("prompts/" + p + ".md").resolve())
    from career_scout.decisions import INSTRUCTIONS, MODEL, PROFILES

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
        database=str(path / "analysis.sqlite3"),
        common_prompt=str(Path("prompts/applications.md").resolve()),
        evaluation_policy="applications-1",
    )
    cfg["routing"].update(calibration_file=str(calibration), workspace=str(path))
    file = path / "config.json"
    file.write_text(json.dumps(cfg))
    values = [clone(profiles["product"], i).vacancy.model_dump(mode="json") for i in range(5)]
    monkeypatch.setattr(
        analysis,
        "read_catalog",
        lambda *a: (values, {"hh": {"policy_json": "{}", "policy_hash": "0" * 64}}),
    )
    return file, cfg, values


def test_intake_advances_across_restarts_and_keeps_pending_older_runs(fixture, monkeypatch):
    file, cfg, values = setup_config(fixture, monkeypatch)
    first = analysis.prepare(file, limit=2, max_calls=0)
    second = analysis.prepare(file, limit=2, max_calls=0)
    third = analysis.prepare(file, limit=2, max_calls=0)
    fourth = analysis.prepare(file, limit=2, max_calls=0)
    assert [x["jobs"] for x in [first, second, third, fourth]] == [2, 2, 1, 0]
    assert first["run_id"] in second["pending_previous_runs"]
    store = AnalysisStore(Path(cfg["database"]))
    assert store.db.execute("SELECT count(*) FROM analysis_admissions").fetchone()[0] == 5
    assert store.db.execute("SELECT count(DISTINCT route_key) FROM run_jobs").fetchone()[0] == 5
    # Recovery after a crash between run_jobs and admission insert.
    store.db.execute("DELETE FROM analysis_admissions")
    store.db.commit()
    intake.initialize(store)
    assert store.db.execute("SELECT count(*) FROM analysis_admissions").fetchone()[0] == 5
    store.close()
    again = analysis.prepare(file, limit=1, max_calls=0, reprocess=True)
    assert again["jobs"] == 1
    # Changed owner parameters are a distinct version, not silently skipped.
    cfg["owner_attributes"] = {"remote_only": True}
    file.write_text(json.dumps(cfg))
    assert analysis.prepare(file, limit=1, max_calls=0)["jobs"] == 1


def test_shortlist_only_recommends_current_version_and_never_outputs_questions(fixture):
    path, config, profiles = fixture
    store = AnalysisStore(path / "shortlist.sqlite3")
    frozen = {"evaluation_policy": "applications-1", "selection_key": "v1"}
    run = store.create_run(frozen, 0)
    rs = [app_input(profiles[p], config, "2026-11-12T00:00:00+00:00") for p in ORDER]
    for r in rs:
        store.save_input(r)
    key = store.add_job(run, rs)
    request = rs[0]
    store.save_result(request, ApplicationResult.model_validate(app_result(request)))
    store.save_outcome(run, key, {"status": "ACCEPTED", "accepted_profile": "product"})
    # Synthetic example publication is updated losslessly for this independent input.
    # Check first against its own publication date, then prove expiry excludes it.
    published = datetime.fromisoformat(request.vacancy.published_at.replace("Z", "+00:00"))
    rows = recommendations(store, now=published + timedelta(hours=1))
    assert len(rows) == 1 and rows[0]["profile"] == "product"
    assert "Вопрос" not in render(rows) and "BORDERLINE" not in render(rows)
    assert recommendations(store, now=published + timedelta(days=15)) == []
    assert recommendations(store, profile="automation", now=published + timedelta(hours=1)) == []
    # A newer result under the same policy must not resurrect an earlier PASS.
    later = store.create_run(frozen, 0)
    store.add_job(later, rs)
    store.save_outcome(later, key, {"status": "REJECTED_BY_SELECTED", "accepted_profile": None})
    assert recommendations(store, now=published + timedelta(hours=1)) == []
    assert store.cached(request).verdict == "PASS"  # historical evidence preserved
    store.close()


def test_grouped_shortlist_keeps_each_assignment_and_source_order():
    rows = [
        {
            "source": source,
            "source_id": str(index),
            "profile": profile,
            "title": f"Role {index}",
            "company": "Example",
            "url": f"https://example.org/{index}",
            "reason": "Matches the saved resume.",
        }
        for index, (profile, source) in enumerate(
            [
                ("business_analyst", "trudvsem"),
                ("business_assistant", "hh"),
                ("product", "superjob"),
                ("automation", "habr"),
                ("product", "hh"),
                ("product", "hh"),
            ]
        )
    ]
    rows[4]["geography"] = {"decision": "KEEP_UNCERTAIN", "reason": "Workplace unspecified."}
    rows[4]["salary_review"] = {"note": "Upper bound unspecified."}
    original = json.dumps(rows)
    text = render_grouped(rows)
    assert text.index("## Product") < text.index("## Автоматизация")
    assert text.index("## Автоматизация") < text.index("## Бизнес-ассистент")
    assert text.index("## Бизнес-ассистент") < text.index("## Бизнес-аналитик")
    assert text.index("https://example.org/4") < text.index("https://example.org/5")
    assert text.index("https://example.org/5") < text.index("https://example.org/2")
    assert "## Product — 3" in text and "### HH — 2" in text
    assert "### Работа России — 1" in text
    assert "Workplace unspecified." in text and "Upper bound unspecified." in text
    assert all(text.count(r["url"]) == 1 for r in rows)
    assert json.dumps(rows) == original


def test_empty_grouped_shortlist_has_no_empty_categories():
    assert render_grouped([]) == render([])


def test_date_only_freshness_and_uncertain_boundary():
    from career_scout.shortlist import publication_time

    now = datetime.fromisoformat("2026-11-12T12:00:00+00:00")
    assert publication_time("2026-11-11", now) is not None
    assert publication_time("2026-10-29", now) is None
    assert publication_time("2026-11-13", now) is None
    assert publication_time("2026-11-12T11:00:00", now) is None
    assert publication_time(None, now) is None
    assert publication_time("broken", now) is None


def test_recorded_application_is_hidden_before_limit_without_erasing_fit(fixture):
    from career_scout import assistant_access, exclusions

    path, config, profiles = fixture
    store = AnalysisStore(path / "application-history.sqlite3")
    run = store.create_run({"evaluation_policy": "applications-1", "selection_key": "v1"}, 0)
    requests = []
    for number in range(2):
        group = [
            app_input(clone(profiles[p], number), config, "2026-11-12T00:00:00+00:00")
            for p in ORDER
        ]
        key = store.add_job(run, group)
        request = group[0]
        requests.append(request)
        store.save_result(request, ApplicationResult.model_validate(app_result(request)))
        store.save_outcome(run, key, {"status": "ACCEPTED", "accepted_profile": "product"})
    now = datetime.fromisoformat("2026-11-12T01:00:00+00:00")
    assistant_access.record(store.db, "sent", "hh", "0", "applied", "hh:verified-topic", "")
    for options in ({}, {"run_id": run}, {"profile": "product"}):
        rows = recommendations(store, limit=1, now=now, **options)
        assert [r["source_id"] for r in rows] == ["1"]
    assert store.cached(requests[0]).verdict == "PASS"
    assert not exclusions.identity_blocked(store.db, "hh", "0")
    assistant_access.record(store.db, "restore", "hh", "0", "reopened", "owner:explicit", "")
    assert len(recommendations(store, now=now)) == 2
    store.close()


def test_critical_confirmed_conflict_cannot_be_recommended(fixture):
    request = app_input(fixture[2]["product"], fixture[1])
    value = app_result(request)
    value["assessments"].append(
        {
            **value["assessments"][0],
            "kind": "requirement",
            "mandatory": True,
            "critical": True,
            "status": "conflict",
        }
    )
    with pytest.raises(ValidationError, match="recommendation_with_critical_gap"):
        ApplicationResult.model_validate(value)


def test_pending_second_evaluation_remains_visible_after_first_pass(fixture):
    store = AnalysisStore(fixture[0] / "pending.sqlite3")
    frozen = {"selection_key": "same"}
    run = store.create_run(frozen, 0)
    rs = list(fixture[2].values())
    route = store.add_job(run, rs)
    intake.initialize(store)
    store.save_outcome(
        run,
        route,
        {
            "status": "ACCEPTED",
            "selected_profiles": ["product", "automation"],
            "outcomes": {"product": {"verdict": "PASS"}, "automation": {"verdict": "ERROR"}},
        },
    )
    assert intake.pending_runs(store, frozen) == [run]
    store.close()


def test_public_application_schema_rejects_questions_and_critical_gaps(fixture):
    from jsonschema import Draft202012Validator

    schema = json.loads(Path("schemas/application-result.schema.json").read_text())
    validator = Draft202012Validator(schema)
    request = app_input(fixture[2]["product"], fixture[1])
    value = app_result(request)
    assert validator.is_valid(value)
    value["assessments"][0]["status"] = "unconfirmed"
    value["assessments"][0]["resume_quote"] = None
    assert not validator.is_valid(value)
    value["verdict"] = "REJECT"
    assert validator.is_valid(value)
    value["questions"] = ["Уточните"]
    assert not validator.is_valid(value)


def test_missing_salary_is_normal_and_known_payment_period_is_preserved(fixture):
    from career_scout.models import Salary
    from career_scout.shortlist import terms

    vacancy = fixture[2]["product"].vacancy.model_copy(deep=True)
    vacancy.attributes.salary = None
    assert isinstance(terms(vacancy), str)
    vacancy.attributes.salary = Salary(
        amount_from=1500.5, amount_to=None, currency="RUB", gross=False, period="hour"
    )
    label = terms(vacancy)
    assert "1 500.5" in label and "в час" in label and "на руки" in label
    vacancy.attributes.salary.period = None
    assert "в месяц" not in terms(vacancy)


def test_soft_preference_conflict_is_not_a_hard_ban(fixture):
    request = app_input(fixture[2]["product"], fixture[1])
    value = app_result(request)
    value["assessments"].append(
        {
            **value["assessments"][0],
            "kind": "requirement",
            "mandatory": True,
            "critical": False,
            "status": "conflict",
            "requirement": "Мягкое предпочтение по типу оформления",
        }
    )
    assert ApplicationResult.model_validate(value).verdict == "PASS"


@pytest.mark.parametrize("bad_reference", [False, True])
def test_revalidation_uses_unchanged_wire_and_never_fixes_invalid_quotes(fixture, bad_reference):
    from test_routed_analysis import batch_wire

    from career_scout.routed_pipeline import initialize, reserve_batch, revalidate_saved_responses

    store = AnalysisStore(fixture[0] / "recover.sqlite3")
    request = app_input(fixture[2]["product"], fixture[1])
    store.save_input(request)
    run = store.create_run({}, 1)
    initialize(store)
    batch = reserve_batch(store, run, "product", [request])
    wire = batch_wire([request])
    row = wire["results"][0]
    row.update(schema_version=3, policy="applications-1")
    for a in row["assessments"]:
        a["critical"] = True
    row["assessments"].append(
        {
            **row["assessments"][0],
            "kind": "requirement",
            "status": "conflict",
            "mandatory": True,
            "critical": False,
        }
    )
    if bad_reference:
        row["assessments"][0]["vacancy_reference"]["from"] = "B1V0001"
    raw = json.dumps(wire)
    store.db.execute(
        "UPDATE batch_attempts SET response_json=?,error='invalid_evaluation_result' WHERE id=?",
        (raw, batch),
    )
    store.db.commit()
    revalidate_saved_responses(store, run)
    assert (store.cached(request) is not None) is (not bad_reference)
    assert store.run(run)["calls"] == 1
    saved = store.db.execute(
        "SELECT response_json,error FROM batch_attempts WHERE id=?", (batch,)
    ).fetchone()
    assert saved["response_json"] == raw and saved["error"] == "invalid_evaluation_result"
    assert store.db.execute("SELECT count(*) FROM validation_recoveries").fetchone()[0] == (
        0 if bad_reference else 1
    )
    store.close()


def test_shortlist_skips_large_nonaccepted_inputs_without_leaking_older_pass(fixture, monkeypatch):
    store = AnalysisStore(fixture[0] / "light-shortlist.sqlite3")
    frozen = {"evaluation_policy": "applications-1", "selection_key": "same"}
    rs = [app_input(fixture[2][p], fixture[1], "2026-11-12T00:00:00+00:00") for p in ORDER]
    original = store.create_run(frozen, 0)
    key = store.add_job(original, rs)
    request = rs[0]
    store.save_result(request, ApplicationResult.model_validate(app_result(request)))
    store.save_outcome(original, key, {"status": "ACCEPTED", "accepted_profile": "product"})
    later = store.create_run(frozen, 0)
    store.add_job(later, rs)  # Newer PENDING is not an exclusion, but supersedes old PASS.

    def no_hydration(_):
        raise AssertionError("Nonaccepted rows must not load full CV/vacancy inputs")

    monkeypatch.setattr(store, "inputs", no_hydration)
    now = datetime.fromisoformat("2026-11-12T01:00:00+00:00")
    assert recommendations(store, now=now) == []
    store.close()
