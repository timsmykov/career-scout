"""Offline acceptance of the real HH path: HTTP transport + SQLite + restart."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from career_scout.cli import main
from career_scout.collector import collect
from career_scout.hh import Credentials, HHClient, Page, SourceError, normalize
from career_scout.storage import Store

START = "2026-11-10T00:00:00+00:00"
END = "2026-11-11T00:00:00+00:00"


def listing(vid="1", **extra):
    return {"id": vid, "name": "Business analyst", "employer": {"id": "9", "name": "Test"}, **extra}


def detail(vid="1", **extra):
    return {**listing(vid), "description": "<p>Full requirements, not a snippet.</p>", **extra}


def page(ids=("1",), *, number=0, pages=None, found=None, size=2):
    found = len(ids) if found is None else found
    pages = (min(found, 2000) + size - 1) // size if pages is None else pages
    return {
        "items": [listing(vid) for vid in ids],
        "page": number,
        "pages": pages,
        "found": len(ids) if found is None else found,
        "per_page": size,
    }


@pytest.fixture
def env(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# unrelated values must survive renewal\nHH_USER_AGENT=CareerScout (test@example.org)\n"
        "HH_CLIENT_ID=fake-client\nHH_CLIENT_SECRET=fake-secret\nHH_ACCESS_TOKEN=old-token\n"
        "SUPERJOB_APP_KEY=fake-sj\n"
    )
    return path


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "ledger.sqlite3")
    yield db
    db.close()


def run(store, *, queries=None, start=START, end=END, ttl=24):
    return store.create(queries or ["analyst"], start, end, 2, ttl)


def client(env, handler, *, budget=50, retries=0, clock=lambda: 1000):
    transport = httpx.MockTransport(handler)
    return HHClient(
        Credentials(env),
        client=httpx.Client(transport=transport),
        interval=0,
        sleep=lambda _: None,
        max_requests=budget,
        retries=retries,
        clock=clock,
    )


def answer(request, *, ids=("1",), **kwargs):
    if request.url.path == "/vacancies":
        return httpx.Response(200, json=page(ids, **kwargs))
    return httpx.Response(200, json=detail(request.url.path.rsplit("/", 1)[-1]))


def test_pages_full_text_and_discoveries_are_deduplicated(store, env):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path == "/vacancies":
            n = int(req.url.params["page"])
            return httpx.Response(
                200, json=page(("1", "2") if n == 0 else ("2", "3"), number=n, pages=2, found=4)
            )
        return httpx.Response(
            200,
            json=detail(
                req.url.path.split("/")[-1],
                description="<p>" + "long full text " * 2000 + "TAIL</p>",
                extra_source_field={"preserved": True},
            ),
        )

    rid = run(store, queries=["analyst", "product"])
    result = collect(store, client(env, handler), rid)
    assert result["status"] == "complete"
    assert result["pages_saved"] == 4
    assert result["full_details"] == result["distinct_vacancies"] == 3
    assert result["requests"] == 7
    assert calls.count("/vacancies/2") == 1
    assert store.db.execute("SELECT COUNT(*) FROM discoveries").fetchone()[0] == 6
    snapshot = store.ready(rid)[0]
    assert snapshot["description"].endswith("TAIL")
    assert len(snapshot["description"]) > 20000
    assert snapshot["source_payload"]["extra_source_field"] == {"preserved": True}


def test_budget_then_resume_does_not_refetch_committed_page(store, env):
    rid = run(store)
    result = collect(store, client(env, answer, budget=1), rid)
    assert (result["status"], result["deferred_details"], result["requests"]) == ("partial", 1, 1)
    paths = []

    def handler(req):
        paths.append(req.url.path)
        return answer(req)

    result = collect(store, client(env, handler), rid)
    assert result["status"] == "complete"
    assert result["requests"] == 2
    assert paths == ["/vacancies/1"]


def test_detail_limit_is_deferred_then_resumable(store, env):
    rid = run(store)
    result = collect(store, client(env, answer), rid, max_details=0)
    assert result["deferred_details"] == 1
    assert result["status"] == "partial"
    assert collect(store, client(env, answer), rid)["status"] == "complete"


def test_crash_after_http_before_page_commit_refetches_same_page(store, env, monkeypatch):
    rid = run(store)
    original = store.save_page

    def crash(*args):
        raise RuntimeError("simulated process death before commit")

    monkeypatch.setattr(store, "save_page", crash)
    with pytest.raises(RuntimeError):
        collect(store, client(env, answer), rid)
    assert store.next_query(rid)["next_page"] == 0
    assert store.report(rid)["requests"] == 1
    monkeypatch.setattr(store, "save_page", original)
    assert collect(store, client(env, answer), rid)["status"] == "complete"
    assert store.report(rid)["requests"] == 3


def test_partial_transaction_rolls_back_rows_and_cursor(store):
    rid = run(store)
    store.db.executescript("""
        CREATE TRIGGER fail_second BEFORE INSERT ON vacancies WHEN NEW.id='2'
        BEGIN SELECT RAISE(ABORT, 'simulated disk write error'); END;
    """)
    with pytest.raises(sqlite3.IntegrityError):
        store.save_page(store.next_query(rid), Page.model_validate(page(("1", "2"))), 24)
    assert store.next_query(rid)["next_page"] == 0
    assert store.report(rid)["distinct_vacancies"] == 0
    assert store.report(rid)["pages_saved"] == 0


def test_reopen_database_after_detail_failure(store, env, tmp_path):
    rid = run(store)

    def handler(req):
        return answer(req) if req.url.path == "/vacancies" else httpx.Response(500)

    result = collect(store, client(env, handler, retries=1), rid)
    assert result["error"] == "server_error"
    assert result["requests"] == 3
    reopened = Store(tmp_path / "ledger.sqlite3")
    try:
        assert collect(reopened, client(env, answer), rid)["status"] == "complete"
        assert reopened.report(rid)["pages_saved"] == 1
    finally:
        reopened.close()


def test_page_two_failure_keeps_cursor(store, env):
    rid = run(store)

    def handler(req):
        if req.url.path == "/vacancies":
            if req.url.params["page"] == "1":
                return httpx.Response(503)
            return httpx.Response(200, json=page(pages=2, found=3))
        return answer(req)

    assert collect(store, client(env, handler), rid)["error"] == "server_error"
    assert store.next_query(rid)["next_page"] == 1
    seen = []

    def resume(req):
        seen.append(req.url.params.get("page"))
        return (
            httpx.Response(200, json=page(("2",), number=1, pages=2, found=3))
            if (req.url.path == "/vacancies")
            else answer(req)
        )

    assert collect(store, client(env, resume), rid)["status"] == "complete"
    assert seen == ["1", None]


@pytest.mark.parametrize(
    "code,expected",
    [(403, "access_denied"), (401, "auth_required"), (302, "unexpected_http_status")],
)
def test_auth_access_redirect_no_bypass(store, env, code, expected):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path == "/token":
            return httpx.Response(200, json={"access_token": "renewed-token"})
        return httpx.Response(code, headers={"Location": "https://other.invalid"})

    rid = run(store)
    result = collect(store, client(env, handler), rid)
    assert result["error"] == expected
    assert calls == (["/vacancies", "/token", "/vacancies"] if code == 401 else ["/vacancies"])
    assert result["requests"] == len(calls)
    assert "fake-secret" not in json.dumps(result)
    if code == 401:
        assert Credentials(env).values["HH_ACCESS_TOKEN"] == "renewed-token"
        assert Credentials(env).values["SUPERJOB_APP_KEY"] == "fake-sj"
        assert env.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "retry_after,delay",
    [
        ("30", 30),
        ("nonsense", 60),
        ("inf", 60),
        (format_datetime(datetime.fromtimestamp(1040, UTC), usegmt=True), 40),
    ],
)
def test_rate_limit_survives_resume_and_new_run(store, env, retry_after, delay):
    rid = run(store)
    limited = client(env, lambda _: httpx.Response(429, headers={"Retry-After": retry_after}))
    result = collect(store, limited, rid)
    assert result["status"] == "blocked"
    assert result["cooldown_until"] == 1000 + delay
    for next_rid in (rid, run(store)):
        forbidden = client(env, lambda _: pytest.fail("must respect saved source cooldown"))
        assert collect(store, forbidden, next_rid)["status"] == "blocked"
    resumed = collect(store, client(env, answer, clock=lambda: 1000 + delay + 1), rid)
    assert resumed["status"] == "complete"


def test_retry_attempts_obey_request_budget(store, env):
    rid = run(store)
    result = collect(store, client(env, lambda _: httpx.Response(500), budget=2, retries=5), rid)
    assert result["error"] == "request_budget_exhausted"
    assert result["requests"] == 2


@pytest.mark.parametrize("status", [404, 410])
def test_removed_card_terminal_not_full(store, env, status):
    rid = run(store)
    c = client(env, lambda r: answer(r) if r.url.path == "/vacancies" else httpx.Response(status))
    result = collect(store, c, rid)
    assert result["status"] == "complete"
    assert result["unavailable_details"] == 1
    assert store.ready(rid) == []


@pytest.mark.parametrize(
    "payload,error",
    [
        (detail("2"), "detail_identity_mismatch"),
        (detail(description=""), "detail_missing_description"),
        (detail(name=None), "detail_schema_drift"),
        (detail(employer="broken"), "invalid_employer_schema"),
    ],
)
def test_detail_drift_stays_pending(store, env, payload, error):
    rid = run(store)
    c = client(
        env,
        lambda r: answer(r) if r.url.path == "/vacancies" else httpx.Response(200, json=payload),
    )
    result = collect(store, c, rid)
    assert result["error"] == error
    assert result["status"] == "partial"
    assert result["deferred_details"] == 1
    assert store.ready(rid) == []


@pytest.mark.parametrize(
    "payload,error",
    [
        ({"items": []}, "search_schema_drift"),
        (page(number=1), "pagination_mismatch"),
        (page((), found=1), "unexpected_empty_page"),
    ],
)
def test_invalid_search_never_advances_cursor(store, env, payload, error):
    rid = run(store)
    result = collect(store, client(env, lambda _: httpx.Response(200, json=payload)), rid)
    assert result["error"] == error
    assert store.next_query(rid)["next_page"] == 0


def test_rejected_rows_prevent_false_complete(store, env):
    rid = run(store)
    result = collect(store, client(env, lambda _: httpx.Response(200, json=page(("bad-id",)))), rid)
    assert result["rejected_rows"] == 1
    assert result["status"] == "partial"


def test_version_history_ttl_refresh_stale_excluded_and_same_content_idempotent(store, env):
    first = run(store)
    collect(store, client(env, answer), first)
    unchanged = run(store)
    assert collect(store, client(env, answer), unchanged)["requests"] == 1
    changed = run(store, ttl=0)
    collect(store, client(env, answer, budget=1), changed)
    assert store.inspect("1")["detail_status"] == "stale"
    assert store.ready(changed) == []
    updated = client(env, lambda _: httpx.Response(200, json=detail(description="<p>New role</p>")))
    collect(store, updated, changed)
    assert len(store.inspect("1")["snapshots"]) == 2
    repeat = run(store, ttl=0)
    collect(
        store,
        client(
            env,
            lambda r: (
                answer(r)
                if r.url.path == "/vacancies"
                else httpx.Response(200, json=detail(description="<p>New role</p>"))
            ),
        ),
        repeat,
    )
    assert len(store.inspect("1")["snapshots"]) == 2


def test_large_result_splits_dates_and_deduplicates_overlaps(store, env):
    rid = run(store)
    ranges = []

    def handler(req):
        if req.url.path != "/vacancies":
            return answer(req)
        a, b = req.url.params["date_from"], req.url.params["date_to"]
        ranges.append((a, b))
        return httpx.Response(200, json=page(found=3000) if (a, b) == (START, END) else page())

    result = collect(store, client(env, handler), rid)
    assert result["status"] == "complete"
    assert result["completed_queries"] == 2
    assert result["distinct_vacancies"] == 1
    assert ranges[1][1] == ranges[2][0]
    assert result["requests"] == 4


def test_irreducible_window_is_honestly_truncated(store, env):
    end = (datetime.fromisoformat(START) + timedelta(seconds=1)).isoformat()
    rid = run(store, end=end)
    result = collect(store, client(env, lambda r: answer(r, found=3000)), rid)
    assert result["truncated_queries"] == 1
    assert result["status"] == "partial"


def test_archived_saved_but_excluded(store, env):
    rid = run(store)
    c = client(
        env,
        lambda r: (
            answer(r)
            if r.url.path == "/vacancies"
            else httpx.Response(200, json=detail(archived=True))
        ),
    )
    result = collect(store, c, rid)
    assert result["archived_details"] == 1
    assert result["status"] == "complete"
    assert store.ready(rid) == []


def test_single_writer_lock_is_released(store, tmp_path):
    other = Store(tmp_path / "ledger.sqlite3")
    try:
        with store.lock():
            with pytest.raises(SourceError, match="collector_already_running"), other.lock():
                pytest.fail("second collector acquired the lock")
        with other.lock():
            pass
    finally:
        other.close()


def test_unknown_schema_rejected(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(SourceError, match="unsupported_database_version"):
        Store(path)


def test_text_normalization_preserves_blocks_without_hidden_text():
    result = normalize(
        detail(
            description="<p>One&nbsp; two</p><ul><li>Three</li></ul>"
            "<script>secret script</script><p>End<br>Next</p>"
        ),
        "1",
    )
    assert result["description"] == "One two\n- Three\nEnd\nNext"
    assert result["source_updated_at"] is None


def test_cli_plan_needs_no_database_credentials_or_network(tmp_path, capsys):
    missing = tmp_path / "unused" / "ledger.sqlite3"
    assert main(["--db", str(missing), "plan", "--query", "аналитик"]) == 0
    assert json.loads(capsys.readouterr().out)["queries"] == ["аналитик"]
    assert not missing.parent.exists()


def test_cli_collect_resume_status_inspect_export(tmp_path, env, monkeypatch, capsys):
    import career_scout.cli as cli

    monkeypatch.setattr(
        cli, "HHClient", lambda creds, **kwargs: client(env, answer, budget=kwargs["max_requests"])
    )
    prefix = ["--db", str(tmp_path / "cli.sqlite3"), "--env", str(env)]
    assert (
        main([*prefix, "collect", "--query", "analyst", "--per-page", "2", "--requests", "1"]) == 2
    )
    rid = json.loads(capsys.readouterr().out)["run_id"]
    assert main([*prefix, "resume", rid]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "complete"
    for command in (["status", rid], ["inspect", "1"]):
        assert main([*prefix, *command]) == 0
        assert json.loads(capsys.readouterr().out)
    target = tmp_path / "export.json"
    assert main([*prefix, "export", rid, "--output", str(target)]) == 0
    capsys.readouterr()
    assert json.loads(target.read_text())["vacancies"][0]["source_id"] == "1"
    before = target.read_bytes()
    assert main([*prefix, "export", rid, "--output", str(target)]) == 3
    assert target.read_bytes() == before
    assert main([*prefix, "resume", "unknown"]) == 3
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["error"] == "unknown_run"


def test_abrupt_process_exit_releases_lock_and_preserves_pending_detail(store, env):
    import subprocess
    import sys
    import textwrap

    rid = run(store)
    code = textwrap.dedent("""
        import os, sys
        from pathlib import Path
        import httpx
        from career_scout.hh import Credentials, HHClient
        from career_scout.storage import Store
        from career_scout.collector import collect
        def handler(req):
            if req.url.path != '/vacancies':
                os._exit(9)
            return httpx.Response(200, json={
                'items': [{'id': '1', 'name': 'Analyst'}], 'page': 0,
                'pages': 1, 'per_page': 2, 'found': 1})
        c = HHClient(Credentials(Path(sys.argv[2])), interval=0,
                     client=httpx.Client(transport=httpx.MockTransport(handler)))
        collect(Store(Path(sys.argv[1])), c, sys.argv[3])
    """)
    process = subprocess.run(
        [sys.executable, "-c", code, str(store.path), str(env), rid],
        capture_output=True,
        timeout=20,
    )
    assert process.returncode == 9, process.stderr.decode()
    assert store.report(rid)["requests"] == 2
    assert store.report(rid)["deferred_details"] == 1
    assert store.report(rid)["status"] == "partial"
    assert collect(store, client(env, answer), rid)["status"] == "complete"


@pytest.mark.parametrize("failure", ["transport", "invalid_json"])
def test_transport_and_invalid_json_are_safe_resumable_errors(store, env, failure):
    def handler(req):
        if failure == "transport":
            raise httpx.ConnectError("do not expose transport text", request=req)
        return httpx.Response(200, content=b"broken json")

    rid = run(store)
    result = collect(store, client(env, handler, retries=1), rid)
    assert result["error"] == ("transport_error" if failure == "transport" else "invalid_json")
    assert result["status"] == "partial"
    assert result["pages_saved"] == 0


def test_renewal_succeeds_and_budget_includes_auth(store, env):
    rid = run(store)

    def handler(req):
        if req.url.path == "/token":
            assert req.method == "POST"
            return httpx.Response(200, json={"access_token": "new-token"})
        if req.headers["Authorization"] == "Bearer old-token":
            return httpx.Response(401)
        return answer(req)

    result = collect(store, client(env, handler, budget=3), rid)
    assert result["requests"] == 3
    assert result["deferred_details"] == 1
    assert result["error"] == "request_budget_exhausted"
    assert collect(store, client(env, handler), rid)["status"] == "complete"


def test_empty_search_is_complete(store, env):
    rid = run(store)
    result = collect(store, client(env, lambda _: httpx.Response(200, json=page((), pages=0))), rid)
    assert result["status"] == "complete"
    assert result["distinct_vacancies"] == 0


def test_listing_change_forces_detail_refresh_without_waiting_for_ttl(store, env):
    first = run(store)
    collect(store, client(env, answer), first)
    second = run(store)
    changed = page()
    changed["items"][0]["name"] = "New position"
    result = collect(
        store, client(env, lambda _: httpx.Response(200, json=changed), budget=1), second
    )
    assert result["deferred_details"] == 1
    assert store.inspect("1")["detail_status"] == "stale"
    assert len(store.inspect("1")["snapshots"]) == 1
    assert store.ready(second) == []


def test_actual_snapshot_matches_published_source_schema(store, env):
    from pathlib import Path

    from jsonschema import Draft202012Validator, FormatChecker

    rid = run(store)
    collect(store, client(env, answer), rid)
    path = Path(__file__).resolve().parents[1] / "schemas/hh-snapshot.schema.json"
    validator = Draft202012Validator(json.loads(path.read_text()), format_checker=FormatChecker())
    validator.validate(store.ready(rid)[0])
    store.export(rid)  # one consistent read snapshot; leaves connection usable
    assert store.report(rid)["status"] == "complete"


def test_inconsistent_page_count_cannot_claim_complete(store, env):
    rid = run(store)
    malformed = page(found=10, pages=1)
    result = collect(store, client(env, lambda _: httpx.Response(200, json=malformed)), rid)
    assert result["error"] == "pagination_inconsistent"
    assert result["status"] == "partial"
    assert store.next_query(rid)["next_page"] == 0


def test_token_cache_preserves_base_credentials_and_survives_reopen(env, tmp_path):
    cache = tmp_path / "auth/hh-token.json"
    original = env.read_bytes()
    credentials = Credentials(env, token_path=cache)
    credentials.save_token("cached-token")
    assert env.read_bytes() == original
    assert cache.stat().st_mode & 0o777 == 0o600
    assert Credentials(env, token_path=cache).values["HH_ACCESS_TOKEN"] == "cached-token"
    credentials.save_token("new-cached-token")
    assert Credentials(env, token_path=cache).values["HH_ACCESS_TOKEN"] == "new-cached-token"
    assert env.read_bytes() == original


def test_token_cache_is_scoped_to_application(env, tmp_path):
    cache = tmp_path / "auth/token.json"
    Credentials(env, token_path=cache).save_token("other-app-token")
    env.write_text(env.read_text().replace("fake-client", "different-client"))
    assert Credentials(env, token_path=cache).values["HH_ACCESS_TOKEN"] == "old-token"


@pytest.mark.parametrize("content", ["broken-json", "[]", '{"access_token":"unscoped"}'])
def test_corrupt_token_cache_fails_safely(env, tmp_path, content):
    cache = tmp_path / "token.json"
    cache.write_text(content)
    with pytest.raises(SourceError, match="invalid_token_cache"):
        Credentials(env, token_path=cache)


def test_401_renewal_uses_cache_without_modifying_readonly_base(store, env, tmp_path):
    cache = tmp_path / "auth/token.json"
    original = env.read_bytes()

    def handler(req):
        if req.url.path == "/token":
            return httpx.Response(200, json={"access_token": "cached-renewal"})
        if req.headers["Authorization"] == "Bearer old-token":
            return httpx.Response(401)
        return answer(req)

    c = client(env, handler)
    c.credentials = Credentials(env, token_path=cache)
    assert collect(store, c, run(store))["status"] == "complete"
    assert env.read_bytes() == original
    assert Credentials(env, token_path=cache).values["HH_ACCESS_TOKEN"] == "cached-renewal"


def test_token_cache_cannot_replace_credentials(env):
    with pytest.raises(SourceError, match="token_cache_overlaps_credentials"):
        Credentials(env, token_path=env)


def test_cli_rejects_token_cache_overlapping_secret(env, tmp_path, monkeypatch, capsys):
    import career_scout.cli as cli

    original = env.read_bytes()
    monkeypatch.setattr(cli, "HHClient", lambda *a, **kw: pytest.fail("must fail before HTTP"))
    result = main(
        [
            "--db",
            str(tmp_path / "cli.sqlite3"),
            "--env",
            str(env),
            "--token-file",
            str(env),
            "collect",
            "--query",
            "analyst",
        ]
    )
    assert result == 3
    assert json.loads(capsys.readouterr().out)["error"] == "token_cache_overlaps_credentials"
    assert env.read_bytes() == original
