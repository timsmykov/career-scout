import json
import sqlite3
from pathlib import Path

import pytest

from career_scout import assistant_access as api
from career_scout.analysis_store import AnalysisStore


@pytest.fixture
def ledger(tmp_path):
    store = AnalysisStore(tmp_path / "analysis.sqlite3")
    yield store.db
    store.close()


def test_events_reconcile_and_conflict(ledger):
    first = api.record(ledger, "op1", "hh", "123", "applied", "owner-message:1", "sent")
    assert api.record(ledger, "op1", "hh", "123", "applied", "owner-message:1", "sent") == first
    with pytest.raises(ValueError, match="operation_id_conflict"):
        api.record(ledger, "op1", "hh", "123", "rejected", "owner-message:1", "sent")
    assert len(api.events(ledger)) == 1


def test_events_require_evidence(ledger):
    with pytest.raises(ValueError, match="evidence_required"):
        api.record(ledger, "op", "hh", "123", "applied", "", "")
    assert api.events(ledger) == []


def test_events_survive_normal_store_reopen(tmp_path):
    path = tmp_path / "analysis.sqlite3"
    store = AnalysisStore(path)
    api.record(store.db, "op1", "hh", "123", "applied", "owner:1", "")
    store.close()
    store = AnalysisStore(path)
    assert len(api.events(store.db)) == 1
    store.close()


def test_shortlist_preserves_owner_decisions(ledger, monkeypatch):
    monkeypatch.setattr(
        api,
        "recommendations",
        lambda *a, **kw: [
            {"source": "hh", "source_id": "123"},
            {"source": "hh", "source_id": "456"},
        ],
    )
    api.record(ledger, "op1", "hh", "123", "dismissed", "owner:1", "")
    api.record(ledger, "op2", "hh", "456", "shown", "delivery:2", "")
    assert api.shortlist(ledger)["items"] == [{"source": "hh", "source_id": "456"}]
    assert api.shortlist(ledger, unseen=True)["items"] == []


def test_only_explicit_reopen_restores_candidate(ledger, monkeypatch):
    monkeypatch.setattr(
        api, "recommendations", lambda *a, **kw: [{"source": "hh", "source_id": "1"}]
    )
    api.record(ledger, "dismiss", "hh", "1", "dismissed", "owner:1", "")
    assert api.shortlist(ledger)["items"] == []
    api.record(ledger, "reopen", "hh", "1", "reopened", "owner:2", "")
    assert len(api.shortlist(ledger)["items"]) == 1


def test_compact_legacy_history_keeps_full_evidence_in_ledger(ledger):
    raw = json.dumps(
        {
            "original": {
                "source_status": "Исторический отказ",
                "properties": {"Следующий шаг": "Проверить оригинал", "Основания": "x" * 10000},
            }
        }
    )
    event = api.record(ledger, "legacy1", "hh", "1", "legacy_observation", "notion:1", raw)
    compact = api.compact_event(event)
    assert compact["detail"]["original_status"] == "Исторический отказ"
    assert "Основания" not in json.dumps(compact, ensure_ascii=False)
    assert api.events(ledger)[0]["detail"] == raw


def test_readonly_connection_does_not_migrate(tmp_path):
    p = tmp_path / "ledger.sqlite3"
    sqlite3.connect(p).close()
    db = api.connect(p)
    assert api.events(db) == []
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        db.execute("CREATE TABLE forbidden(x)")
    db.close()


def test_catalog_pagination_unicode_and_literal_percent():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        "CREATE TABLE vacancies(source,source_id,content_hash,detail_fetched_at,detail_status);"
        "CREATE TABLE normalized_snapshots(source,source_id,content_hash,payload);"
    )
    for i, title in enumerate(["АНАЛИТИК", "Аналитик 100%", "Аналитик 1000"]):
        db.execute("INSERT INTO vacancies VALUES('hh',?,?,'today','full')", (str(i), str(i)))
        db.execute(
            "INSERT INTO normalized_snapshots VALUES('hh',?,?,?)",
            (str(i), str(i), json.dumps({"source": "hh", "source_id": str(i), "title": title})),
        )
    page = api.catalog(db, "аналитик", limit=2)
    assert page["total"] == 3 and page["next_offset"] == 2
    assert api.catalog(db, "100%")["total"] == 1
    assert api.card(db, "hh", "1")["live_platform_checked"] is False
    with pytest.raises(ValueError, match="unknown_full_vacancy"):
        api.card(db, "hh", "missing")


def test_resume_staging_preserves_active_configuration(tmp_path):
    example = Path(__file__).parents[1] / "examples/worker-input-applications.synthetic.json"
    snapshot = json.loads(example.read_text())["resume"]
    resume = tmp_path / "product.json"
    resume.write_text(json.dumps(snapshot))
    cfg = tmp_path / "analysis.json"
    cfg.write_text(json.dumps({"profiles": {"product": resume.name}}))
    before = cfg.read_bytes(), resume.read_bytes()
    text = tmp_path / "new.txt"
    text.write_text(snapshot["text"] + "\nNew confirmed experience.")
    result = api.stage_resume(cfg, "product", text, "verified browser readback")
    assert (cfg.read_bytes(), resume.read_bytes()) == before
    assert result == api.stage_resume(cfg, "product", text, "verified browser readback")
    assert (
        json.loads(Path(result["next_config"]).read_text())["profiles"]["product"]
        == result["resume"]
    )
    assert result["analysis_started"] is False
