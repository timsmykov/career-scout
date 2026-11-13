"""Agent CLI acceptance: real entrypoints, receipts, versions and bounded fake HTTP."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from test_analysis import setup as base_setup
from test_applications import app_input, app_result, setup_config

from career_scout import agent_cli, agent_service
from career_scout.agent_models import ContextInput, RunLimits, SearchPlanInput
from career_scout.agent_state import AgentState, CommandError
from career_scout.analysis_store import AnalysisStore
from career_scout.application import ApplicationResult
from career_scout.hh import Credentials, HHClient


def context_input(count=1):
    return {
        "resumes": [
            {"id": f"cv_{i}", "title": "Врач", "text": "Работаю врачом пятнадцать лет.\n"}
            for i in range(count)
        ],
        "goals": "Нужна клиническая работа, не IT",
        "preferences": [
            {"statement": "Только удалённо или офис в Москве", "priority": "required"},
            "Без чистых продаж",
        ],
    }


def invoke(tmp_path, capsys, *args, data=None):
    args = list(args)
    if data is not None:
        file = tmp_path / "input.json"
        file.write_text(json.dumps(data, ensure_ascii=False))
        args.extend(["--input", str(file)])
    code = agent_cli.main(["--workspace", str(tmp_path / "workspace"), *args])
    out = capsys.readouterr()
    response = json.loads(out.out)
    assert response["schema_version"] == 1 and not out.err
    assert code == (0 if response["status"] == "ok" else 2)
    return response


@pytest.mark.parametrize("count", [1, 7])
def test_context_roundtrip_arbitrary_resumes_and_exact_text(tmp_path, capsys, count):
    value = context_input(count)
    first = invoke(tmp_path, capsys, "context", "create", "--request-id", "create", data=value)
    assert first["status"] == "ok" and len(first["result"]["resumes"]) == count
    assert "document" not in first["result"]
    repeated = invoke(tmp_path, capsys, "context", "create", "--request-id", "create", data=value)
    assert repeated == first
    shown = invoke(tmp_path, capsys, "context", "show", first["result"]["context_id"])
    assert shown["result"]["document"]["resumes"][0]["text"] == value["resumes"][0]["text"]
    assert shown["result"]["document"]["goals"] == value["goals"]


def test_version_conflict_replay_and_immutable_old_context(tmp_path, capsys):
    value = invoke(
        tmp_path, capsys, "context", "create", "--request-id", "c", data=context_input()
    )["result"]
    cid = value["context_id"]
    changed = invoke(
        tmp_path,
        capsys,
        "context",
        "update",
        cid,
        "--if-version",
        "1",
        "--request-id",
        "u",
        data={"goals": "Другая цель"},
    )
    assert changed["result"]["version"] == 2
    assert (
        invoke(
            tmp_path,
            capsys,
            "context",
            "update",
            cid,
            "--if-version",
            "1",
            "--request-id",
            "u",
            data={"goals": "Другая цель"},
        )
        == changed
    )
    conflict = invoke(
        tmp_path,
        capsys,
        "context",
        "update",
        cid,
        "--if-version",
        "1",
        "--request-id",
        "u2",
        data={"goals": "Нельзя перезаписать"},
    )
    assert conflict["error"]["code"] == "version_conflict"
    old = invoke(tmp_path, capsys, "context", "show", cid, "--version", "1")["result"]
    assert old["document"]["goals"] == context_input()["goals"]
    collision = invoke(
        tmp_path, capsys, "context", "create", "--request-id", "c", data=context_input(7)
    )
    assert collision["error"]["code"] == "request_id_conflict"


def test_plan_neutral_defaults_no_hidden_profession_or_experience_cutoff(tmp_path, capsys):
    cid = invoke(tmp_path, capsys, "context", "create", "--request-id", "c", data=context_input())[
        "result"
    ]["context_id"]
    plan = invoke(
        tmp_path,
        capsys,
        "plan",
        "put",
        "--context",
        cid,
        "--request-id",
        "p",
        data={"sources": ["hh"], "queries": ["врач кардиолог"]},
    )
    preview = invoke(tmp_path, capsys, "plan", "preview", plan["result"]["id"])["result"]
    frozen = preview["effective_plans"][0]
    assert (
        frozen["queries"] == ["врач кардиолог"]
        and frozen["filter_policy"]["max_required_years"] is None
    )
    assert (
        frozen["filter_policy"]["domain_terms"] == []
        and "senior" in frozen["filter_policy"]["allowed_grades"]
    )
    assert preview["network_calls"] == 0 and preview["free_text_preferences_applied"] is False
    invoke(
        tmp_path,
        capsys,
        "context",
        "update",
        cid,
        "--if-version",
        "1",
        "--request-id",
        "u",
        data={"goals": "Новые требования"},
    )
    blocked = invoke(
        tmp_path,
        capsys,
        "run",
        "start",
        "--context",
        cid,
        "--plan",
        plan["result"]["id"],
        "--request-id",
        "r",
    )
    assert blocked["error"]["code"] == "plan_context_stale"


def fake_source(tmp_path, calls):
    env = tmp_path / "synthetic.env"
    env.write_text("HH_ACCESS_TOKEN=fake\nHH_USER_AGENT=Test\n")
    publication = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    def factory(run, frozen, budget):
        def handle(request):
            calls.append(request.url.path)
            if request.url.path == "/vacancies":
                return httpx.Response(
                    200,
                    json={
                        "items": [{"id": "1"}, {"id": "2"}],
                        "found": 2,
                        "pages": 1,
                        "page": 0,
                        "per_page": 100,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": request.url.path.rsplit("/", 1)[-1],
                    "name": "Senior врач",
                    "description": "<p>Опыт работы от 10 лет. Клиническая работа.</p>",
                    "published_at": publication,
                    "experience": {"id": "moreThan6"},
                },
            )

        return HHClient(
            Credentials(env),
            client=httpx.Client(transport=httpx.MockTransport(handle)),
            max_requests=budget,
            interval=0,
            retries=0,
        )

    return factory


def setup_run(tmp_path, capsys, budget=2):
    cid = invoke(tmp_path, capsys, "context", "create", "--request-id", "c", data=context_input())[
        "result"
    ]["context_id"]
    pid = invoke(
        tmp_path,
        capsys,
        "plan",
        "put",
        "--context",
        cid,
        "--request-id",
        "p",
        data={"sources": ["hh"], "queries": ["кардиолог"]},
    )["result"]["id"]
    limits = tmp_path / "limits.json"
    limits.write_text(json.dumps({"max_requests": budget}))
    created = invoke(
        tmp_path,
        capsys,
        "run",
        "start",
        "--context",
        cid,
        "--plan",
        pid,
        "--limits",
        str(limits),
        "--request-id",
        "r",
    )
    assert created["result"]["run_status"] == "queued"
    return created["result"]["run_id"]


def test_collection_budget_idempotency_resume_and_no_hidden_filter(tmp_path, capsys, monkeypatch):
    calls = []
    monkeypatch.setattr(agent_service, "source_factory", fake_source(tmp_path, calls))
    rid = setup_run(tmp_path, capsys)
    assert not calls and not (tmp_path / "workspace" / "collector.sqlite3").exists()
    partial = invoke(tmp_path, capsys, "run", "execute", rid, "--request-id", "e1")
    assert partial["result"]["run_status"] == "partial" and len(calls) == 2
    assert (
        invoke(tmp_path, capsys, "run", "execute", rid, "--request-id", "e1") == partial
        and len(calls) == 2
    )
    invoke(tmp_path, capsys, "run", "resume", rid, "--request-id", "no-budget")
    assert len(calls) == 2
    final = invoke(
        tmp_path, capsys, "run", "resume", rid, "--additional-requests", "1", "--request-id", "e2"
    )
    assert final["result"]["run_status"] == "complete" and len(calls) == 3
    assert (
        invoke(
            tmp_path,
            capsys,
            "run",
            "resume",
            rid,
            "--additional-requests",
            "1",
            "--request-id",
            "e2",
        )
        == final
    )
    assert len(calls) == 3
    results = invoke(tmp_path, capsys, "run", "results", rid, "--limit", "1")["result"]
    assert results["total"] == 2 and "description" not in results["items"][0]
    second = invoke(
        tmp_path, capsys, "run", "results", rid, "--limit", "1", "--cursor", results["next_cursor"]
    )["result"]
    assert second["items"][0]["source_id"] != results["items"][0]["source_id"]
    with sqlite3.connect(tmp_path / "workspace" / "collector.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0] == 1
    full = invoke(tmp_path, capsys, "run", "results", rid, "--full")["result"]
    assert "10 лет" in full["items"][0]["description"]


def test_interrupted_operation_requires_new_request_but_preserves_campaign(
    tmp_path, capsys, monkeypatch
):
    rid = setup_run(tmp_path, capsys, budget=10)

    def crash(*a):
        raise KeyboardInterrupt

    monkeypatch.setattr(agent_service, "source_factory", crash)
    assert (
        invoke(tmp_path, capsys, "run", "execute", rid, "--request-id", "e")["error"]["code"]
        == "operation_interrupted"
    )
    assert invoke(tmp_path, capsys, "run", "status", rid)["result"]["run_status"] == "interrupted"
    assert (
        invoke(tmp_path, capsys, "run", "execute", rid, "--request-id", "e")["error"]["code"]
        == "operation_incomplete"
    )
    calls = []
    monkeypatch.setattr(agent_service, "source_factory", fake_source(tmp_path, calls))
    assert (
        invoke(tmp_path, capsys, "run", "resume", rid, "--request-id", "resume")["result"][
            "run_status"
        ]
        == "complete"
    )
    assert len(calls) == 3
    with sqlite3.connect(tmp_path / "workspace" / "collector.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0] == 1


def test_schema_errors_and_read_commands_do_not_initialize_workspace(tmp_path, capsys):
    schema = invoke(tmp_path, capsys, "schema", "--name", "context")
    assert schema["result"]["capabilities"]["generic_context_evaluation"] is False
    assert not (tmp_path / "workspace").exists()
    missing = invoke(tmp_path, capsys, "context", "list")
    assert missing["error"]["code"] == "workspace_not_initialized"
    assert not (tmp_path / "workspace").exists()
    bad = invoke(
        tmp_path,
        capsys,
        "context",
        "create",
        "--request-id",
        "bad",
        data={"resumes": [], "api_key": "DO_NOT_ECHO"},
    )
    assert bad["error"]["code"] == "invalid_input" and "DO_NOT_ECHO" not in json.dumps(bad)
    assert invoke(tmp_path, capsys, "unknown")["error"]["code"] == "invalid_arguments"


@pytest.mark.parametrize("where", ["root", "leaf"])
def test_common_options_work_before_and_after_subcommands(tmp_path, capsys, where):
    p = tmp_path / "input.json"
    p.write_text(json.dumps(context_input()))
    args = ["context", "create", "--input", str(p), "--request-id", "c", "--json"]
    common = ["--workspace", str(tmp_path / "w")]
    assert agent_cli.main([*common, *args] if where == "root" else [*args, *common]) == 0
    assert (
        json.loads(capsys.readouterr().out)["status"] == "ok"
        and (tmp_path / "w" / "control.sqlite3").exists()
    )


def test_foreign_database_is_not_modified(tmp_path):
    workspace = tmp_path / "w"
    workspace.mkdir()
    path = workspace / "control.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE important(value TEXT)")
    before = path.read_bytes()
    with pytest.raises(CommandError, match="not_agent_workspace"):
        AgentState(workspace, write=True)
    assert path.read_bytes() == before


def test_cursor_change_is_visible_not_silent_duplicate():
    first = agent_service.page([1, 2, 3], limit=1)
    assert agent_service.page([1, 2, 3], limit=1, cursor=first["next_cursor"])["items"] == [2]
    with pytest.raises(CommandError, match="cursor_stale"):
        agent_service.page([0, 1, 2, 3], limit=1, cursor=first["next_cursor"])


@pytest.fixture
def legacy(tmp_path):
    return base_setup.__wrapped__(tmp_path)


def test_read_existing_shortlist_and_explanation_without_migrating_database(
    tmp_path, capsys, legacy
):
    path, config, profiles = legacy
    store = AnalysisStore(path / "existing.sqlite3")
    run = store.create_run({"evaluation_policy": "applications-1", "selection_key": "existing"}, 0)
    request = app_input(
        profiles["product"], config, (datetime.now(UTC) - timedelta(days=1)).isoformat()
    )
    key = store.add_job(run, [request])
    store.save_result(request, ApplicationResult.model_validate(app_result(request)))
    store.save_outcome(run, key, {"status": "ACCEPTED", "accepted_profile": "product"})
    store.close()
    before = (path / "existing.sqlite3").read_bytes()
    shown = invoke(
        tmp_path, capsys, "shortlist", "list", "--analysis-db", str(path / "existing.sqlite3")
    )
    assert shown["result"]["total"] == 1 and shown["result"]["agent_context_applied"] is False
    explained = invoke(
        tmp_path,
        capsys,
        "explain",
        "--analysis-db",
        str(path / "existing.sqlite3"),
        "--source",
        "hh",
        "--id",
        request.vacancy.source_id,
    )
    assert explained["result"]["evaluations"][0]["verdict"] == "PASS"
    status = invoke(
        tmp_path, capsys, "analysis", "status", run, "--db", str(path / "existing.sqlite3")
    )
    assert status["result"]["status"] == "complete"
    assert (path / "existing.sqlite3").read_bytes() == before


def test_legacy_analysis_bridge_prepare_replay_and_disabled_models(
    tmp_path, capsys, legacy, monkeypatch
):
    config, cfg, _ = setup_config(legacy, monkeypatch)
    prepared = invoke(
        tmp_path,
        capsys,
        "analysis",
        "prepare",
        "--config",
        str(config),
        "--max-calls",
        "0",
        "--request-id",
        "prepare",
    )
    assert prepared["status"] == "ok", prepared
    assert prepared["result"]["agent_context_applied"] is False
    replay = invoke(
        tmp_path,
        capsys,
        "analysis",
        "prepare",
        "--config",
        str(config),
        "--max-calls",
        "0",
        "--request-id",
        "prepare",
    )
    assert replay == prepared
    rid = prepared["result"]["analysis"]["run_id"]
    disabled = invoke(
        tmp_path,
        capsys,
        "analysis",
        "run",
        rid,
        "--db",
        cfg["database"],
        "--request-id",
        "run-models",
    )
    assert disabled["error"]["code"] == "model_calls_disabled"
    receipt = invoke(tmp_path, capsys, "operation", "show", "run-models")
    assert receipt["result"]["receipt"] == disabled
    missing = invoke(tmp_path, capsys, "analysis", "status", "missing", "--db", cfg["database"])
    assert missing["status"] == "error"


def test_context_compare_and_swap_serializes_competing_writers(tmp_path):
    workspace = tmp_path / "w"

    def create(index):
        state = AgentState(workspace, write=True)
        try:
            return agent_service.context_create(state, context_input(), f"create-{index}")
        finally:
            state.close()

    with ThreadPoolExecutor(2) as pool:
        created = list(pool.map(create, range(2)))
    cid = created[0]["result"]["context_id"]

    def update(index):
        state = AgentState(workspace, write=True)
        try:
            return agent_service.context_update(state, cid, {"goals": str(index)}, 1, f"u-{index}")
        except CommandError as exc:
            return exc.code
        finally:
            state.close()

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(update, range(2)))
    assert results.count("version_conflict") == 1
    assert sum(isinstance(r, dict) and r["result"]["version"] == 2 for r in results) == 1


def test_resume_can_change_credential_file_reference(tmp_path, capsys, monkeypatch):
    rid = setup_run(tmp_path, capsys, budget=10)
    seen = []
    delegate = fake_source(tmp_path, [])

    def factory(run, frozen, budget):
        seen.append(run["env_path"])
        return delegate(run, frozen, budget)

    monkeypatch.setattr(agent_service, "source_factory", factory)
    reference = tmp_path / "reference-only.env"
    result = invoke(
        tmp_path,
        capsys,
        "run",
        "resume",
        rid,
        "--env",
        str(reference),
        "--request-id",
        "resume",
    )
    assert result["result"]["run_status"] == "complete" and seen == [str(reference)]
    assert not reference.exists()


def test_running_lock_prevents_second_executor(tmp_path, capsys):
    import fcntl

    from career_scout.common import digest

    rid = setup_run(tmp_path, capsys)
    root = tmp_path / "workspace" / "run-locks"
    root.mkdir()
    with (root / (digest(rid) + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        blocked = invoke(tmp_path, capsys, "run", "execute", rid, "--request-id", "busy")
        assert blocked["error"]["code"] == "run_busy"
    assert not (tmp_path / "workspace" / "collector.sqlite3").exists()


def test_stdin_and_duplicate_keys_rejected_without_echo(tmp_path, capsys, monkeypatch):
    from io import StringIO

    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(context_input())))
    assert (
        invoke(
            tmp_path,
            capsys,
            "context",
            "create",
            "--input",
            "-",
            "--request-id",
            "stdin",
        )["status"]
        == "ok"
    )
    monkeypatch.setattr("sys.stdin", StringIO('{"resumes": [], "resumes": "SECRET"}'))
    invalid = invoke(
        tmp_path,
        capsys,
        "context",
        "create",
        "--input",
        "-",
        "--request-id",
        "bad-json",
    )
    assert invalid["status"] == "error" and "SECRET" not in json.dumps(invalid)


@pytest.mark.parametrize(
    "change",
    [
        {"resumes": [context_input()["resumes"][0]] * 2},
        {"preferences": [{"statement": " "}]},
        {"preferences": [" "]},
    ],
)
def test_invalid_contexts_do_not_create_partial_contexts(tmp_path, capsys, change):
    invalid = invoke(
        tmp_path,
        capsys,
        "context",
        "create",
        "--request-id",
        "bad",
        data={**context_input(), **change},
    )
    assert invalid["status"] == "error"
    assert invoke(tmp_path, capsys, "context", "list")["result"]["total"] == 0


@pytest.mark.parametrize(
    "name,model",
    [
        ("agent-context", ContextInput),
        ("agent-plan", SearchPlanInput),
        ("agent-limits", RunLimits),
    ],
)
def test_published_agent_schemas_match_runtime(name, model):
    published = json.loads(Path(f"schemas/{name}.schema.json").read_text())
    published.pop("$id")
    published.pop("$schema")
    assert published == model.model_json_schema()
    model.model_validate_json(Path(f"examples/{name}.synthetic.json").read_text())


def test_habr_interval_and_geography_are_independent_of_other_sources():
    plan = SearchPlanInput(
        sources=["hh", "habr"],
        queries=["врач"],
        area_ids=["1"],
        source_options={"habr": {"city_id": 678}},
    )
    hh, habr = plan.compile()
    assert hh["source_options"] == {"area_ids": ["1"]}
    assert habr["source_options"]["city_id"] == 678 and "area_ids" not in habr["source_options"]
    assert hh["runtime"]["interval"] == 1 and habr["runtime"]["interval"] == 3
