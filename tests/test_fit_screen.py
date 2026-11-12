"""Native Decisions triage is conservative, per profile and independently budgeted."""

import json

import pytest
from test_analysis import setup as shared_setup
from test_prescreen import single_payload
from test_routed_analysis import prepare_routed, probs

from career_scout import fit_screen, prescreen
from career_scout.evaluation import EvaluationError
from career_scout.routed_pipeline import aggregate, route_jobs


@pytest.fixture
def fixture(tmp_path):
    return shared_setup.__wrapped__(tmp_path)


def payload(probabilities):
    return {
        "model": "gpt-6-luna",
        "answers": [
            {
                "type": "choice",
                "name": "fit",
                "choice": max(probabilities, key=probabilities.get),
                "probabilities": [{"value": k, "probability": v} for k, v in probabilities.items()],
            }
        ],
    }


def test_stable_resume_prefix_and_no_cross_card_cache_key(fixture):
    from test_routed_analysis import clone

    first = fixture[2]["product"]
    second = clone(first, 2)
    a, b = fit_screen.body(first), fit_screen.body(second)
    assert a["prompt_cache_key"] == b["prompt_cache_key"]
    assert len(a["prompt_cache_key"]) <= 64
    assert a["input"][0]["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
    text = a["input"][0]["content"][0]["text"]
    assert first.resume.text in json.loads(text)["resume"]["text"]
    assert first.evaluation_key not in text
    assert "expected" not in json.loads(text)


def test_uncertain_or_just_below_threshold_is_kept(fixture):
    class Client:
        def request(self, body):
            return {
                "response": payload(
                    {"potential_match": 0.001, "uncertain": 0.005, "clear_mismatch": 0.994}
                )
            }

    value = fit_screen.evaluate(Client(), fixture[2]["product"])
    assert value["excluded"] is False


def test_malformed_answer_is_technical_error(fixture):
    class Client:
        def request(self, body):
            return {"response": {"model": "gpt-6-luna", "answers": []}}

    with pytest.raises(EvaluationError, match="invalid_fit_screen_response"):
        fit_screen.evaluate(Client(), fixture[2]["product"])


@pytest.mark.parametrize("both_excluded", [True, False])
def test_selected_profiles_screen_independently_before_llm(fixture, both_excluded):
    store, rid, frozen, _ = prepare_routed(fixture, [["product", "automation"]])
    with store.db:
        store.db.execute("DELETE FROM routed_selections WHERE run_id=?", (rid,))
    frozen["routing"].update(
        prescreen_policy_hash=prescreen.POLICY_HASH, fit_screen_policy_hash=fit_screen.POLICY_HASH
    )
    seen = []

    class Client:
        def request(self, body):
            if body["questions"][0]["name"] == "primary":
                value = single_payload()
                value["answers"][0].update(
                    choice="product",
                    probabilities=[
                        {"value": p, "probability": v}
                        for p, v in probs(product=0.5, automation=0.5).items()
                    ],
                )
                return {"response": value}
            profile = json.loads(body["input"][0]["content"][0]["text"])["profile_id"]
            seen.append(profile)
            excluded = both_excluded or profile == "product"
            return {
                "response": payload(
                    {
                        "potential_match": 0 if excluded else 1,
                        "uncertain": 0,
                        "clear_mismatch": 1 if excluded else 0,
                    }
                )
            }

    route_jobs(store, rid, frozen, Client())
    aggregate(store, rid)
    row = store.db.execute("SELECT status,outcome_json FROM run_jobs").fetchone()
    assert seen == ["product", "automation"]
    assert row["status"] == ("PRESCREENED_OUT" if both_excluded else "PENDING")
    assert json.loads(row["outcome_json"])["selected_profiles"] == (
        [] if both_excluded else ["automation"]
    )
    assert store.db.execute("SELECT requests FROM routing_budgets").fetchone()[0] == 3
    assert store.db.execute("SELECT COUNT(*) FROM batch_attempts").fetchone()[0] == 0
    store.close()
