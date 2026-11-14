"""Public installation defaults never inherit a candidate or host configuration."""

import json
import os
from pathlib import Path

import pytest

from career_scout import assistant_access, cli
from career_scout.analysis_store import AnalysisStore
from career_scout.common import SourceError
from career_scout.context_models import NativeConnection
from career_scout.decisions import CLI, NATIVE_ROOT, DecisionsClient
from career_scout.filtering import evaluate
from career_scout.search_config import load_config, plan

ROOT = Path(__file__).parents[1]


def test_neutral_search_accepts_unrelated_senior_work_without_a_hidden_ceiling():
    from test_filtering import vacancy

    config = load_config(ROOT / "configs/search.toml")
    assert not config.sources["superjob"].storage_allowed
    assert config.filters.max_required_years is None
    assert config.filters.domain_terms == config.filters.outside_role_names == []
    assert config.search.area_ids == []
    queries = ["врач кардиолог", "senior civil engineer"]
    assert plan(config, queries=queries)["queries"] == queries
    record = vacancy(
        title="Senior civil engineer",
        description="Строительство мостов. Опыт работы от 12 лет.",
        experience="moreThan6",
    )
    assert evaluate(record, config.filters).status != "FILTERED_OUT"


def test_missing_personal_queries_fail_before_credentials_network_or_database(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "Credentials", lambda *a, **kw: pytest.fail("credential access"))
    monkeypatch.setattr(cli, "HHClient", lambda *a, **kw: pytest.fail("network access"))
    database = tmp_path / "unused" / "db.sqlite3"
    with pytest.raises(SourceError, match="search_queries_not_configured"):
        plan(load_config(ROOT / "configs/search.toml"))
    assert cli.main(["--db", str(database), "plan"]) == 3
    assert json.loads(capsys.readouterr().out)["error"] == "search_queries_not_configured"
    assert not database.parent.exists()
    assert cli.main(["--db", str(database), "plan", "--query", "veterinary surgeon"]) == 0
    assert json.loads(capsys.readouterr().out)["queries"] == ["veterinary surgeon"]
    assert not database.parent.exists()


def test_decisions_defaults_have_no_external_deployment_dependency(tmp_path):
    assert CLI == NativeConnection.model_fields["binary"].default == "career-scout-decisions"
    assert NATIVE_ROOT == Path.home() / ".local/share/career-scout/decisions"
    workspace = tmp_path / "decisions"
    workspace.mkdir()
    client = DecisionsClient(workspace, workspace_root=tmp_path, binary="my-routing-connector")
    assert client.workspace == workspace and client.binary == "my-routing-connector"


def test_export_uses_explicit_root_current_user_and_exclusive_private_file(tmp_path, monkeypatch):
    db = tmp_path / "analysis.sqlite3"
    AnalysisStore(db).close()
    root = tmp_path / "exports"
    root.mkdir()
    target = root / "history.json"
    args = [
        "assistant_access",
        "--analysis",
        str(db),
        "--collector",
        str(tmp_path / "not-opened.sqlite3"),
        "--output-root",
        str(root),
        "--output",
        str(target),
        "history",
    ]
    monkeypatch.setattr("sys.argv", args)
    assistant_access.main()
    assert json.loads(target.read_text())["events"] == []
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.stat().st_uid == os.geteuid()
    with pytest.raises(SystemExit):
        assistant_access.main()
    args[args.index("--output") + 1] = str(tmp_path / "outside.json")
    monkeypatch.setattr("sys.argv", args)
    with pytest.raises(SystemExit):
        assistant_access.main()
    assert not (tmp_path / "outside.json").exists()


def test_export_requires_explicit_existing_root_before_opening_database(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "assistant_access",
            "--analysis",
            str(tmp_path / "absent.sqlite3"),
            "--collector",
            str(tmp_path / "absent-collector.sqlite3"),
            "--output",
            str(tmp_path / "out.json"),
            "history",
        ],
    )
    with pytest.raises(SystemExit):
        assistant_access.main()
    assert not (tmp_path / "out.json").exists()
