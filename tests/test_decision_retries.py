"""Only definite temporary HTTP failures get bounded, separately receipted retries."""

import json
from pathlib import Path

import pytest
from test_analysis import setup as shared_setup
from test_routed_analysis import probs

from career_scout import decisions
from career_scout.common import digest
from career_scout.evaluation import EvaluationError


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    setup = shared_setup.__wrapped__(tmp_path)
    monkeypatch.setattr(decisions, "NATIVE_ROOT", tmp_path)
    monkeypatch.setattr(decisions.time, "sleep", lambda delay: None)
    request = decisions.decision_body(setup[2]["product"].vacancy)
    receipt = tmp_path / (digest(request) + "-receipt.json")
    return tmp_path, setup[2]["product"].vacancy, receipt


def success():
    return {
        "ok": True,
        "http_status": 200,
        "response": {
            "model": "gpt-6-luna",
            "answers": [
                {
                    "type": "choice",
                    "name": "primary",
                    "choice": "product",
                    "probabilities": [
                        {"value": p, "probability": v} for p, v in probs(product=1).items()
                    ],
                }
            ],
        },
    }


def test_saved_503_retries_once_then_reuses_success(fixture, monkeypatch):
    path, vacancy, receipt = fixture
    original = {"ok": False, "http_status": 503, "error_type": "server_error"}
    receipt.write_text(json.dumps(original))
    calls = []

    def invoke(args, **kwargs):
        calls.append(args)
        Path(args[args.index("--output") + 1]).write_text(json.dumps(success()))

    monkeypatch.setattr(decisions.subprocess, "run", invoke)
    client = decisions.DecisionsClient(path, max_attempts=3)
    value = client.evaluate(vacancy)
    assert value["probabilities"]["product"] == 1
    assert len(value["attempt_receipts"]) == 2
    assert len(calls) == 1
    assert json.loads(receipt.read_text()) == original
    assert client.evaluate(vacancy)["cached"] is True
    assert len(calls) == 1


@pytest.mark.parametrize("status", [None, 400, 401, 403])
def test_unknown_and_nontransient_receipts_are_never_retried(fixture, monkeypatch, status):
    path, vacancy, receipt = fixture
    receipt.write_text(json.dumps({"ok": False, "http_status": status}))
    monkeypatch.setattr(decisions.subprocess, "run", lambda *a, **kw: pytest.fail("must not retry"))
    with pytest.raises(EvaluationError, match="decisions_failed_or_unknown"):
        decisions.DecisionsClient(path, max_attempts=3).evaluate(vacancy)


def test_three_transient_attempts_are_a_finite_limit(fixture, monkeypatch):
    path, vacancy, receipt = fixture
    receipt.write_text(json.dumps({"ok": False, "http_status": 503}))
    calls = []

    def invoke(args, **kwargs):
        calls.append(args)
        Path(args[args.index("--output") + 1]).write_text(
            json.dumps({"ok": False, "http_status": 503})
        )

    monkeypatch.setattr(decisions.subprocess, "run", invoke)
    with pytest.raises(EvaluationError):
        decisions.DecisionsClient(path, max_attempts=3).evaluate(vacancy)
    assert len(calls) == 2
    assert len(list(path.glob("*receipt.json"))) == 3


@pytest.mark.parametrize(
    "code", ["credit_balance_exhausted", "organization_spend_limit_exceeded", "insufficient_quota"]
)
def test_429_billing_is_never_retried(fixture, monkeypatch, code):
    path, vacancy, receipt = fixture
    receipt.write_text(
        json.dumps(
            {
                "ok": False,
                "http_status": 429,
                "error_type": "insufficient_quota",
                "error_code": code,
            }
        )
    )
    monkeypatch.setattr(decisions.subprocess, "run", lambda *a, **kw: pytest.fail("billing retry"))
    with pytest.raises(EvaluationError):
        decisions.DecisionsClient(path, max_attempts=3).evaluate(vacancy)
