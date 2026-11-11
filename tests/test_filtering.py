import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from career_scout.filtering import evaluate
from career_scout.hh import Page, normalize
from career_scout.normalization import project_snapshot
from career_scout.search_config import load_config
from career_scout.storage import Store

CONFIG = Path(__file__).parents[1] / "configs/search.toml"


@pytest.fixture
def policy():
    return load_config(CONFIG).filters


def vacancy(
    title="Junior product manager",
    description="Работаем над IT-продуктом.",
    experience="between1And3",
    salary=None,
    **extra,
):
    raw = {
        "id": "1",
        "name": title,
        "description": "<p>" + description + "</p>",
        "salary": salary,
        **extra,
    }
    if experience is not None:
        raw["experience"] = {"id": experience}
    return project_snapshot(normalize(raw, "1"))


@pytest.mark.parametrize(
    "title",
    [
        "Intern product manager",
        "Стажёр аналитик",
        "Junior product manager",
        "Middle business analyst",
        "Junior/Middle automation engineer",
    ],
)
def test_allowed_levels_pass_with_technology_and_appropriate_experience(policy, title):
    result = evaluate(vacancy(title=title), policy)
    assert result.status == "KEEP"


@pytest.mark.parametrize(
    "title",
    [
        "Senior product manager",
        "Lead engineer",
        "Head of product",
        "Ведущий бизнес-аналитик",
        "Руководитель отдела автоматизации",
    ],
)
def test_only_explicit_own_higher_grade_is_excluded(policy, title):
    result = evaluate(vacancy(title=title), policy)
    assert result.status == "FILTERED_OUT"
    assert result.criteria["grade"].status == "excluded"
    assert all(x.field == "title" for x in result.criteria["grade"].evidence)


@pytest.mark.parametrize(
    "title",
    [
        "Business assistant to Head of product",
        "Ассистент руководителя направления",
        "Руководитель IT-проектов",
        "Lead generation manager",
        "Менеджер продукта",
    ],
)
def test_unstated_grade_is_kept_instead_of_inferred(policy, title):
    result = evaluate(
        vacancy(title=title, description="В команде senior и опытный руководитель."), policy
    )
    assert result.status == "KEEP_UNKNOWN"
    assert result.criteria["grade"].status == "unknown"


@pytest.mark.parametrize(
    "title",
    ["Middle/Senior product manager", "Junior (не Senior) analyst", "Senior or junior analyst"],
)
def test_mixed_or_negated_title_kept_as_conflict(policy, title):
    result = evaluate(vacancy(title=title), policy)
    assert result.status == "KEEP_UNKNOWN"
    assert result.criteria["grade"].status == "conflict"


@pytest.mark.parametrize("category", ["noExperience", "between1And3", None])
def test_allowed_or_missing_experience_never_excluded(policy, category):
    result = evaluate(vacancy(experience=category), policy)
    assert result.status == ("KEEP_UNKNOWN" if category is None else "KEEP")


@pytest.mark.parametrize("category", ["between3And6", "moreThan6"])
def test_unambiguous_api_experience_outside_owner_limit_is_excluded(policy, category):
    assert evaluate(vacancy(experience=category), policy).status == "FILTERED_OUT"


@pytest.mark.parametrize(
    "description",
    [
        "Опыт работы от 5 лет.",
        "Опыт Python от 4 до 6 лет.",
        "5+ years of experience.",
        "Experience with analytics at least 4 years.",
    ],
)
def test_explicit_own_mandatory_minimum_is_used_when_api_unknown(policy, description):
    result = evaluate(vacancy(description=description, experience=None), policy)
    assert result.criteria["experience"].status == "excluded"
    assert result.status == "FILTERED_OUT"
    assert result.criteria["experience"].evidence


@pytest.mark.parametrize(
    "description",
    [
        "Опыт от 5 лет будет плюсом.",
        "Желателен опыт от 5 лет.",
        "Опыт от 5 лет не требуется.",
        "Будете работать с наставником с опытом от 10 лет.",
        "Наша компания работает 20 лет.",
    ],
)
def test_optional_negated_colleague_and_company_years_do_not_reject(policy, description):
    assert (
        evaluate(vacancy(description=description, experience=None), policy).status == "KEEP_UNKNOWN"
    )


def test_api_text_conflict_is_retained(policy):
    assert (
        evaluate(
            vacancy(experience="between3And6", description="Опыт работы не требуется."), policy
        )
        .criteria["experience"]
        .status
        == "conflict"
    )
    result = evaluate(
        vacancy(experience="between3And6", description="Опыт работы от 1 года."), policy
    )
    assert result.status == "KEEP_UNKNOWN"
    assert result.criteria["experience"].status == "conflict"
    assert (
        evaluate(
            vacancy(experience="between1And3", description="Опыт работы от 5 лет."), policy
        ).status
        == "KEEP_UNKNOWN"
    )
    assert (
        evaluate(
            vacancy(experience="between3And6", description="Опыт работы от 3 лет."), policy
        ).status
        == "KEEP_UNKNOWN"
    )


def test_unknown_domain_is_kept_and_ai_word_is_not_required(policy):
    result = evaluate(vacancy(description="Помогать команде планировать работу."), policy)
    assert result.status == "KEEP_UNKNOWN" and result.criteria["domain"].status == "unknown"
    assert (
        evaluate(vacancy(description="Создаём fintech сервис."), policy).criteria["domain"].status
        == "allowed"
    )
    assert (
        evaluate(vacancy(description="Интеграция сервисов."), policy).criteria["domain"].status
        == "allowed"
    )


def test_outside_domain_requires_both_own_role_and_explicit_industry(policy):
    data = vacancy(
        title="Курьер",
        description="Доставка заказов.",
        experience="noExperience",
        employer={"name": "Test", "industries": [{"name": "Розничная торговля"}]},
    )
    assert evaluate(data, policy).criteria["domain"].status == "excluded"
    data["source_payload"]["employer"].pop("industries")
    assert evaluate(data, policy).status == "KEEP_UNKNOWN"
    data["source_payload"]["employer"]["industries"] = [{"name": "Розничная торговля"}]
    data["description"] = "Мы IT-компания."
    assert evaluate(data, policy).criteria["domain"].status == "allowed"


def test_salary_is_entirely_irrelevant_and_cannot_be_required(policy):
    data = vacancy()
    baseline = evaluate(data, policy).model_dump()
    for amount in (None, 0, 1, 120000, 100000000):
        changed = copy.deepcopy(data)
        changed["attributes"]["salary"] = None if amount is None else {"amount_from": amount}
        changed["source_payload"]["salary"] = None if amount is None else {"from": amount}
        assert evaluate(changed, policy).model_dump() == baseline
    with pytest.raises(ValidationError):
        type(policy).model_validate({**policy.model_dump(), "salary_optional": False})
    with pytest.raises(ValidationError):
        type(policy).model_validate({**policy.model_dump(), "keep_unknown": False})
    with pytest.raises(ValidationError):
        type(policy).model_validate({**policy.model_dump(), "min_salary": 120000})


def add(store, data, policy):
    rid = store.create(["manual"], "2026-11-10", "2026-11-11", 2, 24)
    store.save_page(
        store.next_query(rid),
        Page(items=[{"id": "1", "name": data["title"]}], found=1, pages=1, page=0, per_page=2),
        24,
    )
    store.save_detail(data)
    store.filter_run(rid, policy)
    return rid


def test_filter_history_policy_changes_and_selected_export_do_not_delete_raw_data(tmp_path, policy):
    store = Store(tmp_path / "filters.sqlite3")
    rid = add(
        store,
        normalize(
            {
                "id": "1",
                "name": "Senior analyst",
                "description": "<p>IT</p>",
                "experience": {"id": "between1And3"},
            },
            "1",
        ),
        policy,
    )
    assert store.export(rid)["vacancies"] == []
    assert store.export(rid, include_excluded=True)["vacancies"][0]["source_id"] == "1"
    assert len(store.inspect("1")["snapshots"]) == 1
    changed = policy.model_copy(deep=True)
    changed.allowed_grades.append("senior")
    store.filter_run(rid, changed)
    assert len(store.export(rid)["vacancies"]) == 1
    assert len(store.inspect("1")["filter_history"]) == 2
    store.filter_run(rid, changed)
    assert len(store.inspect("1")["filter_history"]) == 2
    assert store.report(rid)["full_details"] == 1
    store.close()


def test_decision_for_old_content_never_selects_new_version_before_filtering(tmp_path, policy):
    store = Store(tmp_path / "filters.sqlite3")
    original = normalize(
        {
            "id": "1",
            "name": "Junior analyst",
            "description": "<p>IT</p>",
            "experience": {"id": "between1And3"},
        },
        "1",
    )
    rid = add(store, original, policy)
    assert len(store.export(rid)["vacancies"]) == 1
    changed = normalize(
        {
            "id": "1",
            "name": "Senior analyst",
            "description": "<p>IT</p>",
            "experience": {"id": "between1And3"},
        },
        "1",
    )
    store.save_detail(changed)
    assert store.report(rid)["filtering"]["pending"] == 1
    assert store.export(rid)["vacancies"] == []
    store.filter_run(rid)
    assert store.report(rid)["filtering"]["FILTERED_OUT"] == 1
    assert len(store.inspect("1")["snapshots"]) == 2
    store.close()


@pytest.mark.parametrize(
    "title",
    [
        "Ассистент Senior Product Manager",
        "Assistant to Lead engineer",
        "Помощник ведущего менеджера",
    ],
)
def test_grade_of_person_supported_does_not_exclude_assistant(policy, title):
    assert evaluate(vacancy(title=title), policy).criteria["grade"].status == "unknown"
    assert evaluate(vacancy(title="Senior business assistant"), policy).status == "FILTERED_OUT"


def test_contradictory_text_is_kept_and_outside_role_must_be_own_role(policy):
    assert (
        evaluate(vacancy(experience=None, description="Без опыта. Опыт работы от 5 лет."), policy)
        .criteria["experience"]
        .status
        == "conflict"
    )
    data = vacancy(
        title="Менеджер продукта для курьеров",
        description="Продукт доставки.",
        employer={"industries": [{"name": "Розничная торговля"}]},
    )
    assert evaluate(data, policy).status == "KEEP_UNKNOWN"
