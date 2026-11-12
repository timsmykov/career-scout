"""Append-only campaigns through real adapters, transport and SQLite; no network."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from test_hh_collector import detail, page
from test_migration import old_database, table_rows
from test_sources import client as source_client
from test_sources import html_card, listing, sj

from career_scout import cli, migrations
from career_scout.campaigns import Campaigns
from career_scout.common import SourceError
from career_scout.hh import Credentials, HHClient
from career_scout.search_config import load_config
from career_scout.storage import Store

CONFIG = Path(__file__).parents[1] / "configs/search.toml"


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path / "ledger.sqlite3")
    env = tmp_path / "test.env"
    env.write_text("HH_USER_AGENT=CampaignTests\nHH_ACCESS_TOKEN=fake\n")
    yield store, Campaigns(store), env
    store.close()


def hh_factory(env, handler):
    def factory(frozen, budget):
        return HHClient(
            Credentials(env),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            interval=0,
            retries=0,
            max_requests=budget,
        )

    return factory


def create(campaigns, mode="initial", **kw):
    return campaigns.create(
        load_config(CONFIG), mode, sources=["hh"], queries=["test"], lookback_days=None, **kw
    )


def handler_for(ids, calls, *, changed=False, unavailable=()):
    def handler(req):
        calls.append(str(req.url))
        if req.url.path == "/vacancies":
            assert "date_from" in req.url.params
            n = int(req.url.params["page"])
            size = int(req.url.params["per_page"])
            body = page(ids[n * size : (n + 1) * size], number=n, found=len(ids), size=size)
            if changed:
                for item in body["items"]:
                    item["name"] = "Changed title"
            return httpx.Response(200, json=body)
        vid = req.url.path.rsplit("/", 1)[-1]
        if vid in unavailable:
            return httpx.Response(404)
        return httpx.Response(200, json=detail(vid, published_at=datetime.now(UTC).isoformat()))

    return handler


def test_initial_resume_then_daily_delta_never_refetches_changed_or_expired_old_cards(state):
    store, campaigns, env = state
    cid = create(campaigns, per_page=2)
    calls = []
    factory = hh_factory(env, handler_for(["1", "2", "3"], calls))
    first = campaigns.drive(cid, factory, chunk_requests=2, max_rounds=1)
    assert first["status"] == "partial"
    assert first["totals"]["new_descriptions"] == 1
    rid = campaigns.runs(cid)[0]["id"]
    assert campaigns.resolve(rid) == cid
    assert campaigns.drive(cid, factory, chunk_requests=2)["status"] == "complete"
    assert campaigns.report(cid)["totals"]["new_descriptions"] == 3
    assert len([u for u in calls if "/vacancies/" in u]) == 3
    assert len(campaigns.export(cid)["vacancies"]) == 3
    old_rows = store.db.execute("SELECT * FROM vacancies ORDER BY source_id").fetchall()
    old_snapshots = store.db.execute("SELECT * FROM snapshots ORDER BY source_id").fetchall()
    daily = create(campaigns, "new", per_page=2)
    calls.clear()
    result = campaigns.drive(
        daily, hh_factory(env, handler_for(["1", "2", "3", "4"], calls, changed=True))
    )
    assert result["status"] == "complete"
    assert result["totals"]["new_ids"] == result["totals"]["new_descriptions"] == 1
    assert result["totals"]["known_ids"] == 3
    assert len([u for u in calls if "/vacancies/" in u]) == 1
    assert (
        store.db.execute(
            "SELECT * FROM vacancies WHERE source_id!='4' ORDER BY source_id"
        ).fetchall()
        == old_rows
    )
    assert (
        store.db.execute(
            "SELECT * FROM snapshots WHERE source_id!='4' ORDER BY source_id"
        ).fetchall()
        == old_snapshots
    )
    exported = campaigns.export(daily)
    assert [v["source_id"] for v in exported["vacancies"]] == ["4"]
    assert exported["filter_decisions"][0]["source_id"] == "4"
    assert len(campaigns.export(daily, include_known=True)["vacancies"]) == 4
    again = create(campaigns, "new", per_page=2)
    calls.clear()
    assert (
        campaigns.drive(again, hh_factory(env, handler_for(["1", "2", "3", "4"], calls)))["status"]
        == "complete"
    )
    assert not [u for u in calls if "/vacancies/" in u]
    assert campaigns.export(again)["vacancies"] == []
    assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_all_four_sources_and_trud_full_objects_reused(tmp_path, state):
    store, campaigns, env = state
    cid = campaigns.create(
        load_config(CONFIG), "initial", queries=["test"], per_page=2, lookback_days=None
    )
    calls = []

    def factory(frozen, budget):
        source = frozen["source"]
        if source == "hh":
            return hh_factory(env, handler_for(["1"], calls))(frozen, budget)

        def handler(req):
            calls.append(source + req.url.path)
            if req.url.path.endswith("/vacancies") or req.url.path.endswith("/vacancies/"):
                if source == "trudvsem":
                    assert req.url.params["modifiedFrom"].startswith("1970-01-01")
                assert "date_published_from" not in req.url.params
                return httpx.Response(200, json=listing(source, 0, [1], total=1))
            if source == "habr":
                return httpx.Response(200, text=html_card())
            if source == "superjob":
                return httpx.Response(200, json=sj())
            pytest.fail("Full Trudvsem list object must not need a detail GET")

        return source_client(source, tmp_path, handler, max_requests=budget)

    result = campaigns.drive(cid, factory, chunk_requests=2)
    assert result["status"] == "complete", result
    assert result["totals"]["new_descriptions"] == 4
    assert len(campaigns.export(cid)["vacancies"]) == 4
    assert len(set(v["source"] for v in campaigns.export(cid)["vacancies"])) == 4
    assert result["sources"][1]["requests"] == 1
    assert not any("/vacancy/" in p for p in calls)


def test_imports_unfinished_old_card_even_if_not_found_in_current_search(state):
    store, campaigns, env = state
    rid = store.create(["old"], "2020-01-01", "2020-02-01", 2, 24)
    from career_scout.source_types import Page

    store.save_page(store.next_query(rid), Page.model_validate(page(["10"])), 24)
    cid = create(campaigns, "new")
    calls = []
    result = campaigns.drive(cid, hh_factory(env, handler_for([], calls)))
    assert result["status"] == "complete"
    assert result["totals"]["backlog_ids"] == result["totals"]["new_descriptions"] == 1
    assert campaigns.export(cid)["vacancies"][0]["source_id"] == "10"


def test_process_death_reopens_exact_cursor_and_deduplicates(state, monkeypatch):
    store, campaigns, env = state
    cid = create(campaigns, per_page=2)
    calls = []
    original = store.save_page

    def crash(*a, **kw):
        raise RuntimeError("process died before page transaction")

    monkeypatch.setattr(store, "save_page", crash)
    factory = hh_factory(env, handler_for(["1", "2", "3"], calls))
    with pytest.raises(RuntimeError):
        campaigns.drive(cid, factory)
    assert store.next_query(campaigns.runs(cid)[0]["id"])["next_page"] == 0
    monkeypatch.setattr(store, "save_page", original)
    reopened = Store(store.path)
    try:
        assert Campaigns(reopened).drive(cid, factory, chunk_requests=2)["status"] == "complete"
        assert reopened.db.execute("SELECT COUNT(*) FROM acquisitions").fetchone()[0] == 3
    finally:
        reopened.close()


def test_bad_card_is_parked_others_continue_and_manual_retry_is_explicit(state):
    store, campaigns, env = state
    cid = create(campaigns)
    calls = []
    good = handler_for(["1", "2", "3"], calls, unavailable=["3"])

    def handler(req):
        if req.url.path == "/vacancies/1":
            return httpx.Response(200, json={"id": "1", "description": ""})
        return good(req)

    factory = hh_factory(env, handler)
    result = campaigns.drive(cid, factory, max_rounds=1)
    assert result["totals"]["new_descriptions"] == 1
    assert result["totals"]["unavailable"] == 1
    for _ in range(2):
        with store.db:
            store.db.execute("UPDATE detail_failures SET retry_at=0")
        result = campaigns.drive(cid, factory, max_rounds=1)
    assert result["status"] == "partial"
    assert result["totals"]["failed_details"] == result["totals"]["pending_details"] == 1
    result = campaigns.drive(cid, hh_factory(env, handler_for([], calls)), retry_failed=True)
    assert result["status"] == "complete"
    assert result["totals"]["new_descriptions"] == 2


def test_access_denied_is_not_success_but_can_be_manually_resumed(state):
    store, campaigns, env = state
    cid = create(campaigns)
    denied = hh_factory(env, lambda _: httpx.Response(403))
    result = campaigns.drive(cid, denied)
    assert result["sources"][0]["status"] == "blocked"
    assert result["sources"][0]["requests"] == 1
    assert campaigns.drive(cid, hh_factory(env, handler_for([], [])))["status"] == "complete"


def test_rate_limit_cooldown_survives_source_chunks(state):
    store, campaigns, env = state
    cid = create(campaigns)
    factory = hh_factory(env, lambda _: httpx.Response(429, headers={"Retry-After": "120"}))
    result = campaigns.drive(cid, factory, max_rounds=1)
    assert result["sources"][0]["status"] == "blocked"
    assert store.cooldown("hh") is not None
    assert (
        campaigns.drive(cid, factory, max_rounds=1, sleep=lambda _: None)["totals"]["requests"] == 1
    )


@pytest.mark.parametrize("mode", ["initial", "new"])
def test_two_week_window_without_per_source_query_quota(state, mode):
    store, campaigns, env = state
    cid = campaigns.create(load_config(CONFIG), mode)
    for run in campaigns.runs(cid):
        frozen = json.loads(run["plan_json"])
        assert datetime.fromisoformat(frozen["end"]) - datetime.fromisoformat(
            frozen["start"]
        ) == timedelta(days=14)
        assert len(frozen["queries"]) == (16 if run["source"] == "hh" else 12)
        assert frozen["append_only"] is True


def test_unbounded_cap_splits_then_reaches_old_history_and_marks_unsplittable(state):
    store, campaigns, env = state
    cid = create(campaigns)
    rid = campaigns.runs(cid)[0]["id"]
    q = store.next_query(rid)
    assert store.split(q, 2001)
    children = store.db.execute(
        "SELECT * FROM queries WHERE run_id=? AND status='pending'", (rid,)
    ).fetchall()
    assert len(children) == 2
    assert any(child["start"] == "" and child["end"] < q["end"] for child in children)
    with store.db:
        store.db.execute("UPDATE queries SET start=end WHERE status='pending'")
    assert not store.split(store.next_query(rid), 2001)


def test_parent_creation_rolls_back_all_children(state, monkeypatch):
    store, campaigns, env = state
    original = store.create

    def fail_second(*a, **kw):
        if kw["source"] != "hh":
            raise SourceError("synthetic_failure")
        return original(*a, **kw)

    monkeypatch.setattr(store, "create", fail_second)
    with pytest.raises(SourceError):
        campaigns.create(load_config(CONFIG), "initial")
    assert store.db.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0] == 0
    assert store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_v4_migration_preserves_all_old_rows_and_backup(tmp_path):
    path = tmp_path / "old.sqlite3"
    old_database(path)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        migrations.upgrade_v1(db)
        migrations.upgrade_v2(db)
        migrations.upgrade_v3(db)
    before = table_rows(path)
    store = Store(path)
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 5
    assert table_rows(store.backup_path) == before
    after = table_rows(path)
    assert all(after[k] == v for k, v in before.items())
    assert store.backup_path.stat().st_mode & 0o777 == 0o600
    store.close()


def test_campaign_schema_failure_rolls_back_chain(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite3"
    old_database(path)
    before = table_rows(path)
    monkeypatch.setattr(migrations, "CAMPAIGN_DDL", migrations.CAMPAIGN_DDL + ";INVALID SQL")
    with pytest.raises(sqlite3.OperationalError):
        Store(path)
    assert table_rows(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_cli_initial_status_resume_and_new_export(state, monkeypatch, capsys, tmp_path):
    store, campaigns, env = state

    def client_for(frozen, args, options):
        return hh_factory(env, handler_for(["1", "2"], []))(frozen, options["requests"])

    monkeypatch.setattr(cli, "client_for", client_for)
    base = ["--db", str(store.path), "--env", str(env), "--config", str(CONFIG)]
    assert (
        cli.main(
            [
                *base,
                "initial",
                "--source",
                "hh",
                "--query",
                "test",
                "--chunk-requests",
                "1",
                "--max-rounds",
                "1",
            ]
        )
        == 2
    )
    cid = json.loads(capsys.readouterr().out)["campaign_id"]
    assert cli.main([*base, "resume", cid]) == 0
    assert json.loads(capsys.readouterr().out)["totals"]["new_descriptions"] == 2
    assert cli.main([*base, "new", "--source", "hh", "--query", "test"]) == 0
    daily = json.loads(capsys.readouterr().out)["campaign_id"]
    assert cli.main([*base, "status", daily]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "complete"
    output = tmp_path / "delta.json"
    assert cli.main([*base, "export", daily, "--output", str(output)]) == 0
    assert json.loads(output.read_text())["vacancies"] == []


def test_persistent_search_failure_stops_source_after_bounded_transport_retry(state):
    store, campaigns, env = state
    cid = create(campaigns)
    result = campaigns.drive(cid, hh_factory(env, lambda _: httpx.Response(500)))
    assert result["status"] == "partial"
    assert result["sources"][0]["error"] == "server_error"
    assert result["totals"]["requests"] == 1


def test_full_history_cap_is_split_through_real_hh_adapter(state):
    store, campaigns, env = state
    cid = create(campaigns)
    calls = []

    def handler(req):
        if req.url.path != "/vacancies":
            return httpx.Response(200, json=detail(req.url.path.rsplit("/", 1)[-1]))
        calls.append(dict(req.url.params))
        params = req.url.params
        if len(calls) == 1:
            return httpx.Response(200, json=page(["1"], found=2001, size=100))
        # The older unbounded branch is visited as well as the last thirty days.
        return httpx.Response(
            200, json=page(["2"] if params["date_from"].startswith("1970") else ["1"], size=100)
        )

    result = campaigns.drive(cid, hh_factory(env, handler))
    assert result["status"] == "complete", result
    assert result["totals"]["new_descriptions"] == 2
    assert len(calls) == 3
    assert any(
        p["date_from"].startswith("1970") and p["date_to"] != calls[0]["date_to"] for p in calls[1:]
    )


def test_unsplittable_source_window_reports_truncation(state):
    store, campaigns, env = state
    cid = create(campaigns)
    with store.db:
        store.db.execute("UPDATE queries SET start=end")
    result = campaigns.drive(
        cid,
        hh_factory(env, lambda _: httpx.Response(200, json=page(["1"], found=2001, size=100))),
        chunk_requests=1,
        max_rounds=1,
    )
    assert result["status"] == "partial"
    assert result["sources"][0]["truncated_queries"] == 1
    assert result["sources"][0]["discovery_complete"] is False


def test_trud_inline_preserves_same_source_payload_as_dedicated_endpoint(tmp_path):
    from test_sources import trud

    from career_scout.adapters import TrudvsemClient
    from career_scout.models import source_identity
    from career_scout.source_normalization import trudvsem_record

    raw = trud("abc", benefit="All additional source fields stay intact")
    identity = source_identity("trudvsem", "abc", company_code="company").source_id
    prepared = dict(raw, id=identity, _vacancy_id="abc")
    inline = TrudvsemClient().inline_detail(prepared)
    dedicated = trudvsem_record(raw, identity)
    assert inline["source_payload"] == dedicated["source_payload"]
    assert inline["content_hash"] == dedicated["content_hash"]
    assert inline["description"] == dedicated["description"]


def test_repeated_rate_limit_cannot_create_infinite_automatic_loop(state):
    store, campaigns, env = state
    cid = create(campaigns)
    factory = hh_factory(env, lambda _: httpx.Response(429, headers={"Retry-After": "0"}))
    result = campaigns.drive(cid, factory)
    assert result["status"] == "partial"
    assert result["sources"][0]["requests"] == 3
    assert result["sources"][0]["error"] == "rate_limited"


def test_initial_two_week_window_skips_old_listings_and_quarantines_unknown_dates(state):
    store, campaigns, env = state
    cid = campaigns.create(load_config(CONFIG), "initial", sources=["hh"], queries=["test"])
    frozen = json.loads(campaigns.runs(cid)[0]["plan_json"])
    lower = datetime.fromisoformat(frozen["start"])
    upper = datetime.fromisoformat(frozen["end"])
    assert upper - lower == timedelta(days=14)
    dates = {
        "1": (lower - timedelta(seconds=1)).isoformat(),
        "2": lower.isoformat(),
        "3": upper.isoformat(),
        "4": (upper + timedelta(seconds=1)).isoformat(),
        "5": None,
        "6": lower.date().isoformat(),
    }
    fetched = []

    def handler(req):
        if req.url.path == "/vacancies":
            assert req.url.params["date_from"] == frozen["start"]
            assert req.url.params["date_to"] == frozen["end"]
            result = page(list(dates), size=100)
            for item in result["items"]:
                item["published_at"] = dates[item["id"]]
            return httpx.Response(200, json=result)
        vid = req.url.path.rsplit("/", 1)[-1]
        fetched.append(vid)
        return httpx.Response(200, json=detail(vid, published_at=dates[vid]))

    result = campaigns.drive(cid, hh_factory(env, handler))
    assert result["status"] == "complete"
    assert fetched == ["2", "3", "5", "6"]
    assert result["totals"]["outside_publication_window"] == 2
    assert result["totals"]["unknown_publication_date"] == 2
    for include_excluded in (False, True):
        assert {
            v["source_id"]
            for v in campaigns.export(cid, include_excluded=include_excluded)["vacancies"]
        } == {"2", "3"}
    assert store.db.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0] == 6
    assert store.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 4


@pytest.mark.parametrize("source", ["hh", "trudvsem", "habr", "superjob"])
def test_publication_age_uses_publication_not_modification_time(state, source):
    store, campaigns, env = state
    cid = campaigns.create(load_config(CONFIG), "initial", sources=[source], queries=["test"])
    frozen = json.loads(campaigns.runs(cid)[0]["plan_json"])
    lower = datetime.fromisoformat(frozen["start"])
    old = lower - timedelta(days=1)
    now = datetime.fromisoformat(frozen["end"])
    raw = {
        "published_at": old.isoformat(),
        "creation-date": old.date().isoformat(),
        "publishedDate": {"date": old.isoformat()},
        "date_published": old.timestamp(),
        "updated_at": now.isoformat(),
        "date_modify": now.isoformat(),
    }
    assert campaigns.age(cid, source, raw) == "outside"
    assert campaigns.age(cid, source, {}, now.isoformat()) == "recent"
    assert campaigns.age(cid, source, {"date_modify": now.isoformat()}) == "unknown"


def test_previously_saved_old_cards_remain_unchanged_but_not_in_two_week_export(state):
    store, campaigns, env = state
    wide = create(campaigns)
    old = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    calls = []
    base = handler_for(["1"], calls)

    def handler(req):
        if req.url.path == "/vacancies/1":
            return httpx.Response(200, json=detail("1", published_at=old))
        return base(req)

    assert campaigns.drive(wide, hh_factory(env, handler))["status"] == "complete"
    before = store.db.execute("SELECT * FROM snapshots").fetchall()
    limited = campaigns.create(load_config(CONFIG), "initial", sources=["hh"], queries=["test"])
    assert campaigns.drive(limited, hh_factory(env, base))["status"] == "complete"
    assert store.db.execute("SELECT * FROM snapshots").fetchall() == before
    assert campaigns.export(limited, include_excluded=True)["vacancies"] == []
    assert campaigns.report(limited)["totals"]["outside_publication_window"] == 1


def test_daily_does_not_backfill_old_ids_skipped_by_the_initial_window(state):
    store, campaigns, env = state
    old = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path == "/vacancies":
            result = page(["1"], size=100)
            result["items"][0]["published_at"] = old
            return httpx.Response(200, json=result)
        pytest.fail("Old publication must not get a detail request on initial or daily")

    factory = hh_factory(env, handler)
    for mode in ("initial", "new"):
        cid = campaigns.create(load_config(CONFIG), mode, sources=["hh"], queries=["test"])
        result = campaigns.drive(cid, factory)
        assert result["status"] == "complete"
        assert result["totals"]["outside_publication_window"] == 1
        assert result["totals"]["pending_details"] == 0
        assert result["totals"]["new_descriptions"] == 0
        assert campaigns.export(cid)["vacancies"] == []
    assert calls == ["/vacancies", "/vacancies"]
    assert store.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0
