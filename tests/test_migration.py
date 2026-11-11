import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import career_scout.migrations as migrations
from career_scout.common import SourceError, digest
from career_scout.hh import Page, normalize
from career_scout.normalization import project_snapshot
from career_scout.storage import SCHEMA, Store


def old_database(path):
    snapshots = []
    for text in ("old", "current"):
        snapshots.append(
            normalize({"id": "1", "name": "Test", "description": "<p>" + text + "</p>"}, "1")
        )
    plan = {
        "source": "hh",
        "queries": ["test"],
        "per_page": 2,
        "ttl_hours": 24,
        "start": "2026-11-10T00:00:00+00:00",
        "end": "2026-11-11T00:00:00+00:00",
    }
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA + "\nPRAGMA user_version=1;")
        db.execute(
            "INSERT INTO runs(id,created_at,plan_json,requests,pages_saved) VALUES(?,?,?,?,?)",
            ("run", "2026-11-11", json.dumps(plan), 3, 1),
        )
        db.execute(
            "INSERT INTO queries(id,run_id,query,start,end,next_page) VALUES(1,?,?,?,?,?)",
            ("run", "test", plan["start"], plan["end"], 1),
        )
        db.execute(
            "INSERT INTO vacancies VALUES(?,?,?,?,?,?)",
            (
                "1",
                '{"id":"1"}',
                "old-listing-hash",
                "full",
                snapshots[-1]["content_hash"],
                snapshots[-1]["detail_fetched_at"],
            ),
        )
        db.execute(
            "INSERT INTO vacancies VALUES(?,?,?,?,?,?)",
            ("2", '{"id":"2"}', "h", "missing", None, None),
        )
        for item in snapshots:
            # Preserve the exact original JSON serialization, not only parsed equality.
            db.execute(
                "INSERT INTO snapshots VALUES(?,?,?,?)",
                (
                    "1",
                    item["content_hash"],
                    item["detail_fetched_at"],
                    json.dumps(item, ensure_ascii=False, indent=4),
                ),
            )
        db.executemany("INSERT INTO discoveries VALUES(?,?,?)", [("run", 1, "1"), ("run", 1, "2")])
        db.execute(
            "INSERT INTO events(run_id,at,kind,code) VALUES(?,?,?,?)",
            ("run", "2026-11-11", "source_error", "request_budget_exhausted"),
        )
        db.execute("UPDATE source_state SET cooldown_until=42")
    return snapshots


def page(vid):
    return Page(items=[{"id": vid, "name": "Test"}], found=1, pages=1, page=0, per_page=2)


def canonical_other(source, vid):
    item = project_snapshot(
        normalize({"id": "1", "name": "Other", "description": "<p>Other</p>"}, "1")
    )
    item.update(source=source, source_id=vid, url="https://example.org/" + source)
    item["normalization_hash"] = digest(
        {
            "source": source,
            "source_id": vid,
            "content_hash": item["content_hash"],
            "normalizer": item["normalizer_version"],
            "attributes": item["attributes"],
        }
    )
    return item


def test_v1_upgrade_is_lossless_and_idempotent(tmp_path):
    path = tmp_path / "old.sqlite3"
    snapshots = old_database(path)
    with sqlite3.connect(path) as db:
        original = db.execute(
            "SELECT vacancy_id,content_hash,fetched_at,payload FROM snapshots ORDER BY content_hash"
        ).fetchall()
    store = Store(path)
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert [
        tuple(r)
        for r in store.db.execute(
            "SELECT source_id,content_hash,fetched_at,payload FROM snapshots ORDER BY content_hash"
        )
    ] == original
    assert (
        len(store.inspect("1")["snapshots"]) == len(store.inspect("1")["normalized_snapshots"]) == 2
    )
    assert store.inspect("1")["current_hash"] == snapshots[-1]["content_hash"]
    assert store.report("run")["deferred_details"] == 1
    assert store.next_query("run")["next_page"] == 1
    assert store.report("run")["requests"] == 3
    assert store.cooldown() == 42
    assert not store.db.execute("PRAGMA foreign_key_check").fetchall()
    backup = store.backup_path
    store.close()
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2
    assert backup.stat().st_mode & 0o777 == 0o600
    reopened = Store(path)
    assert reopened.backup_path is None
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1
    assert reopened.ready("run")[0]["description"] == "current"
    reopened.close()


def test_migration_failure_rolls_back_schema_rows_and_cursor(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite3"
    old_database(path)
    original = migrations.project_snapshot
    count = 0

    def fail_second(payload):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("simulated projection failure")
        return original(payload)

    monkeypatch.setattr(migrations, "project_snapshot", fail_second)
    with pytest.raises(RuntimeError):
        Store(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT id FROM vacancies ORDER BY id").fetchall() == [("1",), ("2",)]
        assert db.execute("SELECT next_page FROM queries").fetchone()[0] == 1
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert (
            db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'legacy_%'").fetchone()[
                0
            ]
            == 0
        )
    monkeypatch.setattr(migrations, "project_snapshot", original)
    migrated = Store(path)
    assert migrated.db.execute("PRAGMA user_version").fetchone()[0] == 2
    migrated.close()


def test_process_death_during_migration_leaves_recoverable_v1(tmp_path):
    path = tmp_path / "old.sqlite3"
    old_database(path)
    code = """
import os,sys
from pathlib import Path
import career_scout.migrations as migrations
from career_scout.storage import Store
original=migrations.project_snapshot
calls=0
def interrupted(payload):
    global calls
    calls+=1
    if calls==2:os._exit(9)
    return original(payload)
migrations.project_snapshot=interrupted
Store(Path(sys.argv[1]))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(path)], capture_output=True, timeout=20
    )
    assert result.returncode == 9
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2
    recovered = Store(path)
    assert recovered.report("run")["distinct_vacancies"] == 2
    recovered.close()


def test_active_legacy_writer_prevents_migration_and_backup(tmp_path):
    import fcntl

    path = tmp_path / "old.sqlite3"
    old_database(path)
    with path.with_suffix(".sqlite3.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SourceError, match="collector_already_running"):
            Store(path)
        assert not (tmp_path / "backups").exists()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_same_id_in_multiple_sources_keeps_snapshots_discoveries_and_cooldowns_separate(tmp_path):
    store = Store(tmp_path / "multi.sqlite3")
    runs = {}
    for source in ("hh", "habr", "superjob"):
        rid = store.create(["test"], "2026-11-10", "2026-11-11", 2, 24, source=source)
        runs[source] = rid
        store.save_page(store.next_query(rid), page("1"), 24)
        data = (
            normalize({"id": "1", "name": "HH", "description": "<p>HH</p>"}, "1")
            if source == "hh"
            else canonical_other(source, "1")
        )
        store.save_detail(data)
        assert store.finish(rid)["status"] == "complete"
        assert store.ready(rid)[0]["source"] == source
    assert store.db.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0] == 3
    assert store.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 3
    store.unavailable("1", "hh")
    assert store.inspect("1", source="habr")["detail_status"] == "full"
    assert store.inspect("1", source="superjob")["detail_status"] == "full"
    store.finish(runs["hh"], SourceError("rate_limited", 1000))
    assert store.cooldown("hh") == 1000 and store.cooldown("habr") is None
    with pytest.raises(sqlite3.IntegrityError):
        with store.db:
            store.db.execute(
                "INSERT INTO discoveries VALUES(?,?,?,?)",
                (
                    runs["hh"],
                    store.next_query(runs["habr"])["id"] if store.next_query(runs["habr"]) else 2,
                    "habr",
                    "1",
                ),
            )
    store.close()


def test_compound_id_is_enforced_before_listing_is_saved(tmp_path):
    store = Store(tmp_path / "multi.sqlite3")
    rid = store.create(["test"], "2026-11-10", "2026-11-11", 2, 24, source="trudvsem")
    store.save_page(store.next_query(rid), page("unscoped-id"), 24)
    assert store.report(rid)["rejected_rows"] == 1
    assert store.report(rid)["distinct_vacancies"] == 0
    rid = store.create(["test"], "2026-11-10", "2026-11-11", 2, 24, source="trudvsem")
    from career_scout.models import source_identity

    vid = source_identity("trudvsem", "42", company_code="company").source_id
    store.save_page(store.next_query(rid), page(vid), 24)
    store.save_detail(canonical_other("trudvsem", vid))
    assert store.inspect(vid, source="trudvsem")["normalized_snapshots"][0]["source_id"] == vid
    store.close()


def test_backup_failure_never_starts_schema_changes(tmp_path, monkeypatch):
    import career_scout.storage as storage

    path = tmp_path / "old.sqlite3"
    old_database(path)

    def unavailable_disk(*args):
        raise OSError("synthetic full backup disk")

    monkeypatch.setattr(storage, "backup_v1", unavailable_disk)
    with pytest.raises(OSError):
        Store(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2


def test_cli_migration_and_namespaced_inspect_need_no_credentials(tmp_path, monkeypatch, capsys):
    import career_scout.cli as cli

    path = tmp_path / "old.sqlite3"
    old_database(path)
    monkeypatch.setattr(cli, "HHClient", lambda *a, **kw: pytest.fail("no HTTP during migration"))
    assert cli.main(["--db", str(path), "migrate"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["schema_version"] == 2 and Path(output["backup_path"]).exists()
    store = Store(path)
    rid = store.create(["test"], "2026-11-10", "2026-11-11", 2, 24, source="habr")
    store.save_page(store.next_query(rid), page("1"), 24)
    store.save_detail(canonical_other("habr", "1"))
    store.close()
    assert cli.main(["--db", str(path), "inspect", "1", "--source", "habr"]) == 0
    assert json.loads(capsys.readouterr().out)["source"] == "habr"
    assert cli.main(["--db", str(path), "inspect", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["source"] == "hh"


def test_legacy_pending_run_resumes_after_upgrade_without_refetching_saved_card(tmp_path):
    import httpx

    from career_scout.collector import collect
    from career_scout.hh import Credentials, HHClient

    path = tmp_path / "old.sqlite3"
    old_database(path)
    store = Store(path)
    env = tmp_path / ".env"
    env.write_text("HH_USER_AGENT=MigrationTest\nHH_ACCESS_TOKEN=fake\n")
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/vacancies":
            assert request.url.params["page"] == "1"
            return httpx.Response(
                200,
                json={
                    "items": [{"id": "3"}, {"id": "4"}],
                    "found": 4,
                    "pages": 2,
                    "page": 1,
                    "per_page": 2,
                },
            )
        vid = request.url.path.split("/")[-1]
        assert vid != "1"
        return httpx.Response(200, json={"id": vid, "name": "Test", "description": "<p>Full</p>"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        client = HHClient(Credentials(env), client=transport, interval=0)
        result = collect(store, client, "run")
    assert result["status"] == "complete"
    assert result["full_details"] == 4
    assert result["requests"] == 7
    assert "/vacancies/1" not in seen
    assert len(store.inspect("1")["snapshots"]) == 2
    assert store.export("run")["schema_version"] == 2
    store.close()
