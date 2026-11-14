"""Offline public-snapshot guard; live credentials and deployment data are never read."""

import re
import subprocess
from pathlib import Path

from career_scout.common import SourceError
from career_scout.context_models import NativeConnection
from career_scout.search_config import load_config, plan

ROOT = Path(__file__).resolve().parents[1]


def main():
    config = load_config(ROOT / "configs/search.toml")
    assert config.filters.max_required_years is None
    assert not config.filters.domain_terms
    assert not config.filters.outside_role_names and not config.filters.outside_industry_names
    assert set(config.filters.allowed_grades) == {
        "intern",
        "junior",
        "middle",
        "senior",
        "lead",
        "head",
    }
    assert len(config.filters.allowed_experience) == 4
    assert not config.sources["superjob"].storage_allowed
    assert NativeConnection.model_fields["binary"].default == "career-scout-decisions"
    try:
        plan(config)
    except SourceError as exc:
        assert exc.code == "search_queries_not_configured"
    else:
        raise AssertionError("Default collector silently chose a candidate's queries")
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    forbidden = re.compile(r"(?:^|/)(?:private|data|resumes|secrets|credentials)/|(?:^|/)\.env$")
    for name in filter(None, tracked):
        assert not forbidden.search(name), f"Private path tracked: {name}"
    for line in (ROOT / ".env.example").read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() != "LANGSMITH_TRACING":
            assert not value.strip(), f"Nonempty credential example: {key}"
    print("Public snapshot OK: neutral defaults, explicit connectors, no tracked private data.")


if __name__ == "__main__":
    main()
