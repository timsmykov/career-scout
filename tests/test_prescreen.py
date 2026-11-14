"""Native screening cannot silently drop unknowns or shift evidence across cases."""

import json

import pytest
from test_analysis import setup as shared_setup
from test_routed_analysis import clone, prepare_routed, probs

from career_scout import prescreen
from career_scout.batch_worker import CodexBatchWorker, batch_payload
from career_scout.evaluation import EvaluationError
from career_scout.routed_pipeline import aggregate, route_jobs


@pytest.fixture
def fixture(tmp_path):
    return shared_setup.__wrapped__(tmp_path)


def payload(count, experience=0, seniority=0):
    answers = []
    for i in range(count):
        answers.extend(
            [
                {
                    "type": "choice",
                    "name": f"v{i}_primary",
                    "choice": "product",
                    "probabilities": [
                        {"value": p, "probability": v} for p, v in probs(product=1).items()
                    ],
                },
                {"type": "predicate", "name": f"v{i}_experience", "probability": experience},
                {"type": "predicate", "name": f"v{i}_seniority", "probability": seniority},
            ]
        )
    return {"model": "gpt-6-luna", "answers": answers}


def test_twenty_have_unique_cases_questions_and_quotes(fixture):
    requests = [clone(fixture[2]["product"], i) for i in range(20)]
    prompt, schema = batch_payload(requests)
    assert len(schema["properties"]["results"]["items"]["anyOf"]) == 20
    assert "B19V" in prompt
    body = prescreen.body([r.vacancy for r in requests])
    assert len(body["questions"]) == 60
    assert len({q["name"] for q in body["questions"]}) == 60
    assert "id=v19" in body["questions"][-1]["instructions"]
    with pytest.raises(EvaluationError):
        prescreen.body([r.vacancy for r in requests] * 2)


def test_answer_names_not_response_position_define_case():
    value = payload(2)
    value["answers"][-1]["probability"] = 0.999
    value["answers"].reverse()
    result = prescreen.parse(value, 2)
    assert result[0]["gate_probabilities"]["seniority"] == 0
    assert result[1]["gate_probabilities"]["seniority"] == 0.999
    value["answers"].pop()
    with pytest.raises(EvaluationError):
        prescreen.parse(value, 2)


@pytest.mark.parametrize("value", [-1, float("nan"), True, 1.1])
def test_malformed_probabilities_never_become_exclusions(value):
    response = payload(1)
    response["answers"][1]["probability"] = value
    with pytest.raises(EvaluationError):
        prescreen.parse(response, 1)


def test_exclusion_needs_probability_and_local_explicit_evidence(fixture):
    vacancy = fixture[2]["product"].vacancy
    experienced = vacancy.model_copy(update={"description": "Обязателен опыт работы от 5 лет."})
    assert prescreen.evidence(experienced, {"experience": 0.994, "seniority": 0}) is None
    assert (
        prescreen.evidence(experienced, {"experience": 0.999, "seniority": 0})["quote"]
        == "опыт работы от 5 лет"
    )
    for description in (
        "Опыт от 3 лет.",
        "Опыт 1–3 года.",
        "Зарплата не указана.",
        "Компании более 5 лет.",
        "Желателен опыт от 5 лет.",
    ):
        v = vacancy.model_copy(update={"description": description})
        assert prescreen.evidence(v, {"experience": 0.999, "seniority": 0}) is None
    for title in ("Ассистент руководителя", "Middle/Senior Product", "Lead generation manager"):
        v = vacancy.model_copy(update={"title": title})
        assert prescreen.evidence(v, {"experience": 0, "seniority": 0.999}) is None
    senior = vacancy.model_copy(update={"title": "Senior Product Manager"})
    assert (
        prescreen.evidence(senior, {"experience": 0, "seniority": 0.999})["reason"]
        == "explicit_senior_or_lead"
    )


def single_payload(experience=0, seniority=0):
    value = payload(1, experience=experience, seniority=seniority)
    for answer in value["answers"]:
        answer["name"] = answer["name"].removeprefix("v0_")
    return value


def test_single_card_native_requests_resume_without_duplicates(fixture):
    store, run_id, frozen, _ = prepare_routed(fixture, [["product"] for _ in range(20)])
    with store.db:
        store.db.execute("DELETE FROM routed_selections WHERE run_id=?", (run_id,))
    frozen["routing"]["prescreen_policy_hash"] = prescreen.POLICY_HASH
    seen = []

    class Client:
        def request(self, body):
            seen.append(body)
            assert len(body["questions"]) == 3
            assert "cases" not in body["input"][0]["content"][0]["data"]
            return {"response": single_payload(), "key": "proof", "cached": False}

    route_jobs(store, run_id, frozen, Client())
    route_jobs(store, run_id, frozen, Client())
    assert len(seen) == 20
    assert store.db.execute("SELECT requests FROM routing_budgets").fetchone()[0] == 20
    aggregate(store, run_id)
    assert (
        store.db.execute("SELECT COUNT(*) FROM run_jobs WHERE status='PENDING'").fetchone()[0] == 20
    )
    store.close()


def test_twenty_uses_transport_override_without_changing_evaluation_identity(fixture, monkeypatch):
    requests = [clone(fixture[2]["product"], i) for i in range(20)]
    worker = CodexBatchWorker(fixture[1])
    worker.batch_max_input_characters = 400000
    observed = []

    def invoke(prompt, schema, **kw):
        observed.append(kw)
        return {"results": []}, {}

    monkeypatch.setattr(worker, "invoke", invoke)
    worker.batch(requests)
    assert observed == [{"max_input_characters": 400000}]
    assert requests[0].model_config_hash == fixture[1].key()


def test_single_other_is_kept_as_routing_result_without_evaluator(fixture):
    store, rid, frozen, _ = prepare_routed(fixture, [["product"]])
    with store.db:
        store.db.execute("DELETE FROM routed_selections WHERE run_id=?", (rid,))
    frozen["routing"]["prescreen_policy_hash"] = prescreen.POLICY_HASH

    class Client:
        def request(self, body):
            value = single_payload()
            value["answers"][0].update(
                choice="other",
                probabilities=[{"value": p, "probability": v} for p, v in probs(other=1).items()],
            )
            return {"response": value, "key": "single"}

    route_jobs(store, rid, frozen, Client())
    aggregate(store, rid)
    assert store.db.execute("SELECT status FROM run_jobs").fetchone()[0] == "ROUTING_REVIEW"
    assert store.db.execute("SELECT requests FROM routing_budgets").fetchone()[0] == 1
    store.close()


def test_confirmed_hard_exclusion_never_reaches_evaluator(fixture):
    store, rid, frozen, _ = prepare_routed(fixture, [])
    requests = [clone(r, 1, description="Обязателен опыт от 5 лет.") for r in fixture[2].values()]
    store.add_job(rid, requests, policy="decisions-one-two-1")
    frozen["routing"]["prescreen_policy_hash"] = prescreen.POLICY_HASH

    class Client:
        def request(self, body):
            return {"response": single_payload(experience=1), "key": "batch"}

        def evaluate(self, vacancy):
            pytest.fail("hard exclusion needs no second role call")

    route_jobs(store, rid, frozen, Client())
    aggregate(store, rid)
    assert store.db.execute("SELECT status FROM run_jobs").fetchone()[0] == "PRESCREENED_OUT"
    assert store.db.execute("SELECT COUNT(*) FROM batch_attempts").fetchone()[0] == 0
    store.close()


@pytest.mark.parametrize(
    "title",
    [
        "Product Manager военного сервиса",
        "Разработчик ПО для военнослужащих",
        "Бизнес-ассистент; приветствуется служба",
        "Бизнес-аналитик оборонной компании",
        "Оператор БПЛА",
    ],
)
def test_local_occupation_rule_does_not_reject_industry_or_ambiguous_roles(fixture, title):
    vacancy = fixture[2]["product"].vacancy.model_copy(update={"title": title})
    assert prescreen.local_occupation_exclusion(vacancy) is None


def test_direct_outside_occupation_is_persisted_before_native_calls(fixture):
    from career_scout.evaluation import make_input

    store, rid, frozen, _ = prepare_routed(fixture, [])
    with store.db:
        store.db.execute(
            "UPDATE analysis_runs SET config_json=json_set(config_json,"
            "'$.evaluation_policy','applications-1') WHERE id=?",
            (rid,),
        )
    title = "Военнослужащий по контракту / Доброволец"
    requests = []
    for request in fixture[2].values():
        vacancy = clone(request, 100).vacancy.model_copy(update={"title": title})
        requests.append(
            make_input(
                vacancy=vacancy,
                resume=request.resume,
                filters=request.filters,
                filter_version=request.filter_version,
                rubric=request.rubric,
                rubric_version=request.rubric_version,
                common_instructions=request.common_instructions,
                config=fixture[1],
                evaluation_date=request.evaluation_date,
            )
        )
    store.add_job(rid, requests, policy="decisions-one-two-1")
    frozen["routing"]["prescreen_policy_hash"] = prescreen.POLICY_HASH

    class Client:
        def request(self, body):
            pytest.fail("direct outside occupation must not reach Decisions")

    route_jobs(store, rid, frozen, Client())
    aggregate(store, rid)
    route_jobs(store, rid, frozen, Client())
    assert store.db.execute("SELECT status FROM run_jobs").fetchone()[0] == "PRESCREENED_OUT"
    assert store.db.execute("SELECT requests FROM routing_budgets").fetchone()[0] == 0
    assert store.db.execute("SELECT COUNT(*) FROM batch_attempts").fetchone()[0] == 0
    decision = store.db.execute("SELECT decision_json FROM routed_selections").fetchone()[0]
    proof = json.loads(decision)
    assert proof["provider"] == "local_rule"
    assert proof["exclusion"]["quote"] == title
    assert "probabilities" not in proof
    assert store.db.execute("SELECT COUNT(*) FROM vacancy_exclusions").fetchone()[0] == 1

    # A following civilian card is screened normally; the excluded one stays closed.
    store.add_job(rid, [clone(r, 101) for r in fixture[2].values()], policy="decisions-one-two-1")
    calls = []

    class FollowingClient:
        def request(self, body):
            calls.append(body)
            assert body["input"][0]["content"][0]["data"]["title"] != title
            return {"response": single_payload(), "key": "next-card"}

    route_jobs(store, rid, frozen, FollowingClient())
    aggregate(store, rid)
    assert len(calls) == 1
    assert store.db.execute("SELECT requests FROM routing_budgets").fetchone()[0] == 1
    assert dict(store.db.execute("SELECT status,COUNT(*) FROM run_jobs GROUP BY status")) == {
        "PRESCREENED_OUT": 1,
        "PENDING": 1,
    }
    store.close()


def test_twenty_independent_evaluations_use_one_batch(fixture):
    from test_routed_analysis import reply_for

    from career_scout.routed_pipeline import evaluate_queues

    store, rid, frozen, _ = prepare_routed(fixture, [["product"]] * 20, max_calls=1)
    frozen["routing"].update(batch_size=20, max_batch_characters=400000)
    sizes = []

    class Worker:
        def __init__(self, config):
            pass

        def cancel(self):
            pass

        def batch(self, requests):
            sizes.append(len(requests))
            return reply_for(requests)

    assert evaluate_queues(store, rid, frozen, Worker) is None
    assert sizes == [20]
    assert (
        store.db.execute("SELECT COUNT(*) FROM run_jobs WHERE status='ACCEPTED'").fetchone()[0]
        == 20
    )
    store.close()


def test_native_failure_stops_dispatch_and_drains_four_inflight(fixture):
    import threading

    store, rid, frozen, _ = prepare_routed(fixture, [["product"]] * 20)
    with store.db:
        store.db.execute("DELETE FROM routed_selections WHERE run_id=?", (rid,))
    frozen["routing"]["prescreen_policy_hash"] = prescreen.POLICY_HASH
    barrier = threading.Barrier(4, timeout=10)
    calls = []

    class Client:
        def request(self, body):
            calls.append(body)
            barrier.wait()
            raise EvaluationError("native_unavailable", fatal=True)

    with pytest.raises(EvaluationError, match="native_unavailable"):
        route_jobs(store, rid, frozen, Client())
    assert len(calls) == 4
    assert store.db.execute("SELECT requests FROM routing_budgets").fetchone()[0] == 4
    assert store.db.execute("SELECT COUNT(*) FROM routed_selections").fetchone()[0] == 0
    store.close()
