import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from career_scout import cli
from career_scout.common import SourceError
from career_scout.hh import HHClient
from career_scout.search_config import load_config, plan
from career_scout.storage import Store

CONFIG = Path(__file__).parent / "fixtures/search.technology-junior.toml"


def test_default_plan_has_focused_and_broad_queries_for_all_four_roles():
    config = load_config(CONFIG)
    result = plan(config, now=datetime(2026, 11, 11, tzinfo=UTC))
    assert len(result["queries"]) == len(set(result["queries"])) == 16
    assert result["start"] == "2026-11-04T00:00:00+00:00"
    assert result["source_options"]["area_ids"] == []
    assert result["filter_policy"]["salary_optional"] is True
    for family, queries in config.families.items():
        assert all(q in result["queries"] for q in queries)
        assert any(
            " AND " in q and family in origins for q, origins in result["query_families"].items()
        )
    assert result["queries"][4:8] == [items[0] for items in config.families.values()]


def test_manual_queries_are_exact_deduplicated_override_and_config_identity_is_stable():
    config = load_config(CONFIG)
    result = plan(config, queries=["analyst", "analyst"], days=2, per_page=10)
    assert result["queries"] == ["analyst"]
    assert result["query_families"] == {"analyst": ["manual"]}
    assert result["per_page"] == 10
    assert result["filter_policy_hash"] == config.filters.key()
    assert plan(config)["config_hash"] == result["config_hash"]
    changed = config.filters.model_copy(deep=True)
    changed.allowed_grades.append("senior")
    assert changed.key() != config.filters.key()


@pytest.mark.parametrize(
    "replace",
    [
        ("area_ids = []", 'area_ids = ["bad"]'),
        ("salary_optional = true", "salary_optional = false"),
        ("keep_unknown = true", "keep_unknown = false"),
        ('focus_terms = ["IT", "финтех", "ИИ", "ML"]', 'focus_terms = [""]'),
        ("requests = 50", "requests = 0"),
        ("schema_version = 2", "schema_version = 999"),
    ],
)
def test_invalid_configuration_fails_before_network_or_database(
    tmp_path, capsys, monkeypatch, replace
):
    config = tmp_path / "invalid.toml"
    config.write_text(CONFIG.read_text().replace(*replace))
    database = tmp_path / "unused" / "db.sqlite3"
    monkeypatch.setattr(cli, "HHClient", lambda *a, **kw: pytest.fail("no network"))
    assert cli.main(["--config", str(config), "--db", str(database), "collect"]) == 3
    assert json.loads(capsys.readouterr().out) == {"error": "invalid_search_config"}
    assert not database.parent.exists()


def test_plan_does_not_open_credentials_or_database(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "Credentials", lambda *a, **kw: pytest.fail("no credentials"))
    database = tmp_path / "unused" / "db.sqlite3"
    assert cli.main(["--config", str(CONFIG), "--db", str(database), "plan"]) == 0
    assert len(json.loads(capsys.readouterr().out)["queries"]) == 16
    assert not database.parent.exists()


def test_collect_resume_filters_full_cards_and_keeps_frozen_config(tmp_path, monkeypatch, capsys):
    env = tmp_path / "test.env"
    env.write_text("HH_USER_AGENT=OfflineTest\nHH_ACCESS_TOKEN=fake\n")
    config = tmp_path / "search.toml"
    config.write_text(CONFIG.read_text().replace("area_ids = []", 'area_ids = ["1", "2"]'))
    database = tmp_path / "db.sqlite3"
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/vacancies":
            params = request.url.params
            assert params["no_magic"] == "true"
            assert params.get_list("area") == ["1", "2"]
            assert not {"experience", "industry", "salary", "only_with_salary", "label"} & set(
                params
            )
            assert params["text"] == "manual"
            return httpx.Response(
                200,
                json={
                    "items": [{"id": "1"}, {"id": "2"}, {"id": "3"}],
                    "page": 0,
                    "pages": 1,
                    "found": 3,
                    "per_page": 100,
                },
            )
        vid = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={
                "id": vid,
                "name": {"1": "Junior analyst", "2": "Senior analyst", "3": "Аналитик"}[vid],
                "description": "<p>IT-продукт</p>",
                "salary": None,
                "experience": {"id": "between1And3"},
            },
        )

    def client(credentials, **kwargs):
        return HHClient(
            credentials,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            **{**kwargs, "interval": 0},
        )

    monkeypatch.setattr(cli, "HHClient", client)
    prefix = ["--db", str(database), "--env", str(env), "--config", str(config)]
    assert cli.main([*prefix, "collect", "--query", "manual", "--requests", "2"]) == 2
    report = json.loads(capsys.readouterr().out)
    rid = report["run_id"]
    assert report["filtering"]["KEEP"] == 1
    with __import__("sqlite3").connect(database) as db:
        frozen = db.execute("SELECT plan_json FROM runs").fetchone()[0]
    config.unlink()  # Resume must not consult changed/missing config or change the query plan.
    assert cli.main([*prefix, "resume", rid, "--requests", "4"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["filtering"]["KEEP"] == 1
    assert report["filtering"]["KEEP_UNKNOWN"] == 1
    assert report["filtering"]["FILTERED_OUT"] == 1
    store = Store(database)
    assert store.run(rid)["plan_json"] == frozen
    assert len(store.ready(rid)) == 3
    assert len(store.export(rid)["vacancies"]) == 2
    assert len(store.export(rid, include_excluded=True)["vacancies"]) == 3
    assert seen.count("/vacancies/1") == seen.count("/vacancies") == 1
    store.close()
    config.write_text(
        CONFIG.read_text().replace(
            '["intern", "junior", "middle"]', '["intern", "junior", "middle", "senior"]'
        )
    )
    before = len(seen)
    assert cli.main([*prefix, "refilter", rid]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["filtering"]["FILTERED_OUT"] == 0
    assert len(seen) == before  # Refilter is entirely offline.
    store = Store(database)
    assert store.run(rid)["plan_json"] == frozen
    assert len(store.inspect("2")["filter_history"]) == 2
    store.close()


def test_invalid_override_does_not_create_database(tmp_path):
    with pytest.raises(SourceError, match="invalid_search_settings"):
        plan(load_config(CONFIG), per_page=101)
