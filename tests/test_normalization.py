import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from career_scout.hh import normalize
from career_scout.models import Identity, Salary, Vacancy, source_identity
from career_scout.normalization import hh_attributes, project_snapshot


def raw_snapshot(**extra):
    return normalize(
        {"id": "1", "name": "Test role", "description": "<p>Complete text</p>", **extra}, "1"
    )


@pytest.mark.parametrize("source", ["hh", "habr", "superjob"])
def test_namespaced_identity_has_no_url_or_employer_dependency(source):
    assert source_identity(source, "42").model_dump() == {"source": source, "source_id": "42"}


def test_compound_trudvsem_key_is_unambiguous_and_requires_company():
    a = source_identity("trudvsem", "c", company_code="a:b")
    b = source_identity("trudvsem", "b:c", company_code="a")
    assert a.source_id != b.source_id
    assert json.loads(a.source_id) == ["a:b", "c"]
    with pytest.raises(ValueError):
        source_identity("trudvsem", "42")
    with pytest.raises(ValidationError):
        Identity(source="trudvsem", source_id="42")
    with pytest.raises(ValidationError):
        Identity(source="trudvsem", source_id='["a", "b"]')


def test_unknown_salary_and_experience_do_not_turn_into_zero():
    result = hh_attributes({})
    assert result.salary is None and result.experience is None
    assert result.work_formats == []
    assert result.issues == result.conflicts == []


def test_salary_preserves_explicit_zero_one_sided_amount_and_net():
    result = hh_attributes(
        {
            "salary_range": {
                "from": 0,
                "to": 123456.75,
                "currency": "RUR",
                "gross": False,
                "mode": {"id": "MONTH"},
                "frequency": {"id": "TWICE_PER_MONTH"},
            }
        }
    )
    assert result.salary == Salary(
        amount_from=0,
        amount_to=123456.75,
        currency="RUB",
        gross=False,
        period="month",
        payout_frequency="twice_monthly",
    )
    assert result.field_sources["salary"] == ["source_payload.salary_range"]
    assert result.issues == result.conflicts == []


@pytest.mark.parametrize(
    "mode,period",
    [
        ("MONTH", "month"),
        ("HOUR", "hour"),
        ("SHIFT", "shift"),
        ("FLY_IN_FLY_OUT", "rotation"),
        ("SERVICE", "service"),
    ],
)
def test_explicit_salary_periods_and_frequency_are_independent(mode, period):
    result = hh_attributes(
        {
            "salary_range": {
                "from": 1000,
                "currency": "USD",
                "gross": True,
                "mode": {"id": mode},
                "frequency": {"id": "PER_PROJECT"},
            }
        }
    )
    assert result.salary.period == period
    assert result.salary.payout_frequency == "per_project"
    assert result.salary.amount_to is None
    assert result.salary.currency == "USD" and result.salary.gross is True


def test_modern_salary_is_not_mixed_with_legacy_monthly_estimate():
    result = hh_attributes(
        {
            "salary_range": {"from": 1000, "mode": {"id": "HOUR"}},
            "salary": {"from": 150000, "to": 200000, "currency": "RUR", "gross": True},
        }
    )
    assert result.salary.amount_from == 1000
    assert result.salary.amount_to is None
    assert result.salary.currency is result.salary.gross is None
    assert result.salary.period == "hour"


def test_legacy_period_gross_currency_are_not_invented():
    result = hh_attributes({"salary": {"from": 100, "currency": "eur"}})
    assert result.salary.amount_from == 100 and result.salary.currency == "EUR"
    assert result.salary.period is result.salary.gross is None
    assert result.field_sources["salary"] == ["source_payload.salary"]


@pytest.mark.parametrize("invalid", [True, -100, float("nan"), float("inf"), "120000"])
def test_invalid_salary_bounds_are_unknown_with_evidence(invalid):
    result = hh_attributes({"salary": {"from": invalid, "to": 200000}})
    assert result.salary.amount_from is None
    assert result.salary.amount_to == 200000
    assert "salary.from.invalid_number" in result.issues


def test_reversed_bounds_are_preserved_and_flagged_as_conflict():
    result = hh_attributes(
        {"salary": {"from": 200000, "to": 100000, "gross": "false", "currency": "???"}}
    )
    assert result.salary.amount_from == 200000 and result.salary.amount_to == 100000
    assert result.salary.gross is result.salary.currency is None
    assert result.conflicts == ["salary.reversed_bounds"]
    assert len(result.issues) == 2


@pytest.mark.parametrize(
    "source,category,minimum,maximum,inclusive",
    [
        ("noExperience", "no_experience", 0, 0, True),
        ("between1And3", "one_to_three", 1, 3, True),
        ("between3And6", "three_to_six", 3, 6, True),
        ("moreThan6", "over_six", 6, None, False),
    ],
)
def test_experience_categories_preserve_open_six_year_boundary(
    source, category, minimum, maximum, inclusive
):
    result = hh_attributes({"experience": {"id": source}}).experience
    assert (result.category, result.min_years, result.max_years, result.min_inclusive) == (
        category,
        minimum,
        maximum,
        inclusive,
    )


def test_unrecognized_source_values_remain_unknown_and_raw_is_unmodified():
    raw = {
        "salary_range": {"from": 100, "mode": {"id": "NEW_UNIT"}, "frequency": {"id": "NEW_FREQ"}},
        "experience": {"id": "new_experience"},
        "work_format": [{"id": "TELEPATHY"}],
    }
    original = copy.deepcopy(raw)
    out = hh_attributes(raw)
    assert raw == original
    assert out.salary.period is out.salary.payout_frequency is out.experience is None
    assert out.work_formats == []
    assert len(out.issues) == 4


@pytest.mark.parametrize("schedule", ["fullDay", "flexible", "shift"])
def test_schedule_does_not_imply_office_or_remote(schedule):
    assert hh_attributes({"schedule": {"id": schedule}}).work_formats == []


def test_multi_choice_work_format_is_not_itself_a_conflict():
    result = hh_attributes(
        {"work_format": [{"id": "ON_SITE"}, {"id": "HYBRID"}, {"id": "ON_SITE"}]}
    )
    assert result.work_formats == ["hybrid", "on_site"]
    assert result.conflicts == []
    assert hh_attributes({"schedule": {"id": "remote"}}).work_formats == ["remote"]


def test_explicit_work_format_retained_when_legacy_schedule_disagrees():
    result = hh_attributes({"work_format": [{"id": "ON_SITE"}], "schedule": {"id": "remote"}})
    assert result.work_formats == ["on_site"]
    assert result.conflicts == ["work_format.schedule_disagrees"]


def test_projection_keeps_raw_source_and_content_version_immutable():
    original = raw_snapshot(
        salary_range={"from": 100000, "currency": "RUR", "mode": {"id": "MONTH"}}
    )
    before = copy.deepcopy(original)
    result = project_snapshot(original)
    assert original == before
    assert result["source_payload"] == original["source_payload"]
    assert result["content_hash"] == original["content_hash"]
    assert result["source_normalizer_version"] == "hh-1"
    assert result["normalizer_version"] == "common-1" and result["schema_version"] == 2
    assert result["attributes"]["salary"]["currency"] == "RUB"
    assert project_snapshot(original) == result
    assert project_snapshot(result) == result
    changed = copy.deepcopy(original)
    changed["detail_fetched_at"] = "2026-11-11T00:00:00+00:00"
    assert project_snapshot(changed)["normalization_hash"] == result["normalization_hash"]


def test_common_schema_matches_actual_model():
    schema = json.loads((Path(__file__).parents[1] / "schemas/vacancy-v2.schema.json").read_text())
    expected = Vacancy.model_json_schema()
    assert {k: v for k, v in schema.items() if not k.startswith("$")} == {
        k: v for k, v in expected.items() if not k.startswith("$")
    }
    assert schema["$defs"] == expected["$defs"]


def test_common_projection_detects_attribute_or_identity_hash_tampering():
    result = project_snapshot(raw_snapshot())
    result["source"] = "habr"
    with pytest.raises(ValidationError, match="normalization_hash_mismatch"):
        project_snapshot(result)


def test_malformed_schedule_id_is_reported_without_guessing_work_mode():
    result = hh_attributes({"schedule": {"id": ["remote"]}})
    assert result.work_formats == []
    assert result.issues == ["work_format.schedule.invalid_value"]


def test_currency_alias_does_not_redenominate_legacy_belarusian_amounts():
    result = hh_attributes({"salary": {"from": 100000, "currency": "BYR"}})
    assert result.salary.currency == "BYR"
    assert result.salary.amount_from == 100000
