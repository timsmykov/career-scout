"""Provider contracts with synthetic HTTP/CLI replies, no billed requests."""

import json
import sys
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from test_agent_cli import invoke
from test_analysis import setup as base_setup
from test_context_engine import document, prepared, vacancy
from test_routed_analysis import batch_wire, clone

from career_scout import context_engine
from career_scout.agent_skill import bundle, install
from career_scout.agent_state import CommandError
from career_scout.analysis_store import AnalysisStore
from career_scout.claude_worker import ClaudeBatchWorker, command, environment
from career_scout.common import digest
from career_scout.evaluation import EvaluationError, EvaluatorConfig
from career_scout.model_providers import PROVIDERS, APIBatchWorker, batch_worker
from career_scout.models import Vacancy


@pytest.fixture
def fixture(tmp_path):
    return base_setup.__wrapped__(tmp_path)


def wrapped(wire, protocol):
    text = json.dumps(wire)
    if protocol == "responses":
        return {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 80, "output_tokens": 20},
        }
    if protocol == "messages":
        return {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 80, "output_tokens": 20},
        }
    if protocol == "gemini":
        return {
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}],
            "usageMetadata": {"promptTokenCount": 80, "candidatesTokenCount": 20},
        }
    return {
        "choices": [{"finish_reason": "stop", "message": {"content": text}}],
        "usage": {"prompt_tokens": 80, "completion_tokens": 20},
    }


@pytest.mark.parametrize("provider", PROVIDERS)
def test_all_api_protocols_validate_actual_batch_evidence(provider, fixture, monkeypatch):
    _, _, requests = fixture
    config = EvaluatorConfig(
        provider=provider, model="synthetic-model", api={"key_env": "TEST_MODEL_KEY"}
    )
    monkeypatch.setenv("TEST_MODEL_KEY", "synthetic-key")
    request = clone(requests["product"], 1, config=config)
    wire = batch_wire([request])
    endpoint, _, protocol = PROVIDERS[provider]
    calls = []

    def handle(req):
        calls.append(req)
        assert str(req.url).startswith(endpoint)
        assert req.method == "POST" and "synthetic-key" not in str(req.url)
        body = json.loads(req.content)
        assert "tools" not in body and "synthetic-key" not in req.content.decode()
        assert request.evaluation_key in req.content.decode()
        if protocol == "responses":
            assert body["store"] is False and body["text"]["format"]["type"] == "json_object"
        elif protocol == "messages":
            assert req.headers["x-api-key"] == "synthetic-key" and body["max_tokens"] == 16000
        elif protocol == "gemini":
            assert req.headers["x-goog-api-key"] == "synthetic-key"
            assert body["generationConfig"]["responseMimeType"] == "application/json"
        else:
            assert body["response_format"]["type"] == "json_object"
            assert req.headers["authorization"] == "Bearer synthetic-key"
            if provider == "openrouter":
                assert body["provider"] == {"require_parameters": True, "allow_fallbacks": False}
        return httpx.Response(200, json=wrapped(wire, protocol))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        reply = APIBatchWorker(config, client=client).batch([request])
    assert len(calls) == 1 and not reply.errors
    assert reply.replies[request.evaluation_key].result["verdict"] == "PASS"
    assert reply.usage == {"input_tokens": 80, "output_tokens": 20, "cached_input_tokens": 0}
    assert "synthetic-key" not in config.model_dump_json()


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, "model_authentication_failed"),
        (403, "model_access_denied"),
        (429, "model_rate_limited"),
        (302, "model_request_rejected"),
        (400, "model_request_rejected"),
        (500, "model_outcome_unknown"),
    ],
)
def test_api_errors_do_not_follow_redirects_leak_or_retry(status, expected, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    seen = []

    def handle(req):
        seen.append(req)
        return httpx.Response(
            status,
            headers={"location": "https://untrusted.invalid"},
            text="synthetic-secret private resume provider body",
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        worker = APIBatchWorker(EvaluatorConfig(provider="openai", model="test"), client=client)
        with pytest.raises(EvaluationError, match=expected) as failure:
            worker.invoke("JSON", {})
    assert failure.value.fatal and len(seen) == 1
    assert "synthetic-secret" not in str(failure.value)


def test_timeout_is_unknown_and_cannot_reenter_queue(tmp_path, monkeypatch):
    state, p, _ = prepared(tmp_path, count=1)
    config = EvaluatorConfig(provider="openai", model="synthetic")
    # New context to freeze an actual API connection before any queue work.
    p = context_engine.prepare(
        state,
        state.db.execute("SELECT id FROM contexts").fetchone()[0],
        None,
        [vacancy(2).model_dump(mode="json")],
        {"model": config.model_dump(), "external_calls_enabled": True},
        100,
        4,
        0,
    )
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    calls = []

    def fail(req):
        calls.append(req)
        raise httpx.ReadTimeout("private request body", request=req)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:

        def factory(c):
            return APIBatchWorker(c, client=client)

        with closing(AnalysisStore(Path(p["database"]))) as store:
            assert (
                context_engine.execute(store, p["run_id"], worker_factory=factory)
                == "model_outcome_unknown"
            )
            with pytest.raises(EvaluationError, match="batch_outcome_unknown"):
                context_engine.execute(
                    store,
                    p["run_id"],
                    worker_factory=factory,
                    retry_errors=True,
                    additional_calls=3,
                )
    assert len(calls) == 1
    state.close()


def test_provider_settings_preserve_old_config_hash_and_require_model():
    old = {
        "provider": "codex_subscription",
        "model": "gpt-6-luna",
        "reasoning_effort": "max",
        "timeout_seconds": 300,
        "max_input_characters": 100000,
        "max_attempts_per_profile": 2,
    }
    assert EvaluatorConfig().key() == digest(old)
    for provider in [*PROVIDERS, "claude_subscription"]:
        with pytest.raises(ValidationError):
            EvaluatorConfig(provider=provider)
    for bad in [
        {"api": {"key_env": "sk-secret-value"}},
        {"provider": "anthropic", "model": "test", "api": {"output_mode": "schema"}},
        {"provider": "gemini", "model": "test", "api": {"reasoning_effort": "high"}},
        {"provider": "claude_subscription", "model": "sonnet", "api": {}},
        {"provider": "openai", "model": "test", "executable": "codex"},
        {"provider": "openai", "model": "test", "api_key": "synthetic-key"},
    ]:
        with pytest.raises(ValidationError):
            EvaluatorConfig.model_validate(bad)


def test_env_reference_not_copied_and_missing_key_does_not_call(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = EvaluatorConfig(provider="openai", model="test")
    with pytest.raises(EvaluationError, match="key_missing"):
        APIBatchWorker(config).invoke("JSON", {})
    path = tmp_path / "models.env"
    path.write_text('OPENAI_API_KEY="synthetic-only"\n')
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=wrapped({"ok": True}, "responses"))
        )
    ) as client:
        assert APIBatchWorker(config, client=client, env_path=path).invoke("JSON", {})[0] == {
            "ok": True
        }
    assert "synthetic-only" not in config.model_dump_json()


@pytest.mark.parametrize("protocol", ["responses", "messages", "gemini", "chat"])
def test_truncation_and_tool_calls_are_not_success(protocol, monkeypatch):
    provider = {"responses": "openai", "messages": "anthropic", "gemini": "gemini", "chat": "groq"}[
        protocol
    ]
    monkeypatch.setenv(PROVIDERS[provider][1], "synthetic")
    response = wrapped({"results": []}, protocol)
    if protocol == "responses":
        response["status"] = "incomplete"
    elif protocol == "messages":
        response["stop_reason"] = "tool_use"
    elif protocol == "gemini":
        response["candidates"][0]["content"]["parts"] = [{"functionCall": {}}]
    else:
        response["choices"][0]["message"]["tool_calls"] = [{"type": "function"}]
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response))
    ) as client:
        with pytest.raises(EvaluationError, match="model_invalid_response"):
            APIBatchWorker(EvaluatorConfig(provider=provider, model="test"), client=client).invoke(
                "JSON", {}
            )


def fake_claude(tmp_path, result, *, auth="claude.ai", body=None):
    path = tmp_path / "claude-fake"
    flags = "--safe-mode --tools --strict-mcp-config --setting-sources --settings "
    flags += "--no-session-persistence --disable-slash-commands"
    auth_json = json.dumps({"loggedIn": True, "authMethod": auth})
    final = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": json.dumps(result),
            "usage": {"input_tokens": 4, "output_tokens": 2},
        }
    )
    path.write_text(
        f"#!{sys.executable}\nimport sys,json,os\n"
        f"if '--help' in sys.argv:\n print({flags!r});sys.exit()\n"
        f"if sys.argv[1:3]==['auth','status']:\n print({auth_json!r});sys.exit()\n"
        "assert os.environ.get('ANTHROPIC_API_KEY') is None\n"
        "assert os.environ.get('ANTHROPIC_AUTH_TOKEN') is None\n"
        "assert sys.argv[sys.argv.index('--tools')+1]==''\n"
        "assert '--bare' not in sys.argv and '--safe-mode' in sys.argv\n"
        "assert sys.stdin.read()\n" + (body or f"print({final!r})\n")
    )
    path.chmod(0o700)
    return str(path)


def test_claude_subscription_validates_batch_and_strips_keys(tmp_path, fixture, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-api-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "synthetic-token")
    config = EvaluatorConfig(provider="claude_subscription", model="sonnet")
    request = clone(fixture[2]["product"], 1, config=config)
    binary = fake_claude(tmp_path, batch_wire([request]))
    worker = ClaudeBatchWorker(config, binary=binary)
    reply = worker.batch([request])
    assert not reply.errors and request.evaluation_key in reply.replies
    assert reply.usage["input_tokens"] == 4
    assert "ANTHROPIC_API_KEY" not in environment()
    assert "--dangerously-skip-permissions" not in command(config, binary)
    assert isinstance(batch_worker(config), ClaudeBatchWorker)


def test_claude_rejects_api_login_and_unexpected_tools(tmp_path):
    config = EvaluatorConfig(provider="claude_subscription", model="sonnet")
    binary = fake_claude(tmp_path, {}, auth="api_key")
    with pytest.raises(EvaluationError, match="subscription_login_required"):
        ClaudeBatchWorker(config, binary=binary).invoke("test", {})
    binary = fake_claude(
        tmp_path,
        {},
        body="print(json.dumps({'type':'assistant','message':{'content':[{'type':'tool_use'}]}}))\n",
    )
    with pytest.raises(EvaluationError, match="worker_tool_use_not_allowed"):
        ClaudeBatchWorker(config, binary=binary).invoke("test", {})


def test_skill_install_is_packaged_idempotent_and_preserves_user_edits(tmp_path):
    result = install("codex", tmp_path / "skills")
    target = Path(result["path"])
    assert result["installed"] and (target / "references/workflow.md").is_file()
    assert not install("codex", tmp_path / "skills")["installed"]
    (target / "SKILL.md").write_text("Owner customizations")
    with pytest.raises(CommandError, match="skill_destination_conflict"):
        install("codex", tmp_path / "skills")
    assert (target / "SKILL.md").read_text() == "Owner customizations"
    other = tmp_path / "other"
    other.mkdir()
    (other / "career-scout").symlink_to(target, target_is_directory=True)
    with pytest.raises(CommandError, match="skill_destination_conflict"):
        install("claude", other)
    assert bundle().joinpath("SKILL.md").is_file()


def test_cli_discovery_skill_install_and_mode_aware_doctor(tmp_path, capsys, monkeypatch):
    assert len(invoke(tmp_path, capsys, "provider", "list")["result"]["api"]) == 10
    assert invoke(tmp_path, capsys, "skill", "show")["result"]["name"] == "career-scout"
    assert invoke(
        tmp_path,
        capsys,
        "skill",
        "install",
        "--agent",
        "claude",
        "--directory",
        str(tmp_path / "skills"),
    )["result"]["installed"]
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    invoke(
        tmp_path,
        capsys,
        "connection",
        "put",
        "default",
        "--request-id",
        "conn",
        data={"model": {"provider": "openai", "model": "synthetic"}},
    )
    one = invoke(tmp_path, capsys, "context", "create", "--request-id", "c1", data=document())[
        "result"
    ]["context_id"]
    two = invoke(tmp_path, capsys, "context", "create", "--request-id", "c2", data=document(2))[
        "result"
    ]["context_id"]
    single = invoke(tmp_path, capsys, "doctor", "--context", one)["result"]
    assert (
        single["ready"] and single["decisions_required"] is False and single["network_calls"] == 0
    )
    multiple = invoke(tmp_path, capsys, "doctor", "--context", two)["result"]
    assert not multiple["ready"] and multiple["decisions_required"] is True
    assert multiple["next"] == ["decisions_connection"]


def test_codex_unclassified_failure_has_unknown_delivery():
    from career_scout.codex_worker import provider_error

    error = provider_error([], "connection disappeared after submit")
    assert error.fatal and "unknown" in error.code


def test_legacy_resume_blocks_unknown_delivery_before_budget_changes(tmp_path, fixture):
    from career_scout.analysis import run_pending

    with closing(AnalysisStore(tmp_path / "legacy.sqlite3")) as store:
        run = store.create_run({"external_calls_enabled": True}, 3)
        store.add_job(run, list(fixture[2].values()))
        attempt = store.reserve(run, fixture[2]["product"].evaluation_key)
        store.finish_attempt(attempt, error="model_outcome_unknown")
        before = dict(store.run(run))
        with pytest.raises(EvaluationError, match="outcome_unknown"):
            run_pending(store, run, retry_errors=True, additional_calls=5)
        assert dict(store.run(run)) == before


def test_api_cli_executes_frozen_context_with_private_env_and_no_duplicate_batch(
    tmp_path, capsys, monkeypatch
):
    from test_context_engine import answer

    from career_scout.context_models import EvaluationConnection
    from career_scout.evidence import documents

    cid = invoke(tmp_path, capsys, "context", "create", "--request-id", "c", data=document())[
        "result"
    ]["context_id"]
    cfg = EvaluationConnection(model={"provider": "openai", "model": "synthetic"}, batch_size=20)
    invoke(
        tmp_path,
        capsys,
        "connection",
        "put",
        "default",
        "--request-id",
        "conn",
        data=cfg.model_dump(),
    )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([vacancy(i).model_dump(mode="json") for i in range(21)]))
    prepared_run = invoke(
        tmp_path,
        capsys,
        "evaluation",
        "prepare",
        "--context",
        cid,
        "--catalog",
        str(catalog),
        "--max-calls",
        "4",
        "--max-decisions",
        "0",
        "--request-id",
        "prep",
    )["result"]
    path, run = prepared_run["database"], prepared_run["run_id"]
    with closing(AnalysisStore(Path(path))) as store:
        frozen = json.loads(store.run(run)["config_json"])
    connection = EvaluationConnection.model_validate(frozen["connection"])
    by_key = {}
    for raw in json.loads(catalog.read_text()):
        request = context_engine.inputs(frozen["context"], Vacancy.model_validate(raw), connection)[
            "cv0"
        ]
        by_key[request.evaluation_key] = request
    env = tmp_path / "model.env"
    env.write_text("OPENAI_API_KEY=synthetic-env-key\n")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    actual_client = httpx.Client
    batches = []

    def handle(req):
        assert req.headers["authorization"] == "Bearer synthetic-env-key"
        prompt = json.loads(req.content)["input"]
        original = prompt.split("\n\nReturn only a JSON object satisfying this JSON Schema:")[0]
        cases = json.loads(original.rsplit("\n\n", 1)[1])["cases"]
        batches.append([c["evaluation_key"] for c in cases])
        values = []
        for index, case in enumerate(cases):
            request = by_key[case["evaluation_key"]]
            v, r = documents(request, f"B{index}V")
            value = answer(request).model_dump(mode="json")
            a = value["assessments"][0]
            a.pop("vacancy_quote")
            a.pop("resume_quote")
            a.update(
                vacancy_reference={"from": v.ids[0], "to": v.ids[0]},
                resume_reference={"from": r.ids[0], "to": r.ids[0]},
            )
            values.append(value)
        return httpx.Response(200, json=wrapped({"results": values}, "responses"))

    def client_factory(**kwargs):
        return actual_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr("career_scout.model_providers.httpx.Client", client_factory)
    result = invoke(
        tmp_path,
        capsys,
        "evaluation",
        "run",
        run,
        "--db",
        path,
        "--env",
        str(env),
        "--enable-calls",
        "--request-id",
        "execute",
    )["result"]
    assert result["status"] == "complete" and result["outcomes"] == {"ACCEPTED": 21}
    assert sorted(map(len, batches)) == [1, 20]
    assert len({k for batch in batches for k in batch}) == 21
    invoke(
        tmp_path,
        capsys,
        "evaluation",
        "resume",
        run,
        "--db",
        path,
        "--env",
        str(env),
        "--request-id",
        "resume",
    )
    assert len(batches) == 2
    with closing(AnalysisStore(Path(path))) as store:
        assert (
            "synthetic-env-key"
            not in store.db.execute(
                "SELECT config_json FROM analysis_runs WHERE id=?", (run,)
            ).fetchone()[0]
        )


@pytest.mark.parametrize("protocol", ["responses", "messages", "gemini", "chat"])
def test_absent_usage_is_unknown_not_zero_tokens(protocol):
    from career_scout.model_providers import normalized_usage

    assert normalized_usage({}, protocol) is None


@pytest.mark.parametrize("provider", ["openai", "gemini", "openrouter"])
def test_native_schema_is_sent_once_and_no_tools_are_declared(provider, monkeypatch):
    monkeypatch.setenv(PROVIDERS[provider][1], "synthetic")
    config = EvaluatorConfig(provider=provider, model="synthetic", api={"output_mode": "schema"})
    schema = {"type": "object", "properties": {"unique_marker": {"type": "string"}}}
    _, _, body, _ = APIBatchWorker(config).request("JSON", schema)
    assert json.dumps(body).count("unique_marker") == 1
    assert "tools" not in body
