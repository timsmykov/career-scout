"""Installed-package product acceptance in an isolated container, without network.

Job HTTP and the Codex executable are controlled simulators. This verifies the
product workflow and validation, not model accuracy or live account access.
"""

import argparse
import subprocess

SCENARIO = r"""
import contextlib, io, json, subprocess, sys, tempfile, os
from pathlib import Path
from datetime import UTC, datetime, timedelta
import importlib.metadata
import httpx
from career_scout import agent_cli, agent_service
from career_scout.hh import Credentials, HHClient

root = Path(tempfile.mkdtemp(prefix="career-product-"))
workspace = root / "workspace"
checks = []


def call(*args, document=None, expected="ok"):
    argv = ["--workspace", str(workspace), *args]
    if document is not None:
        file = root / "input.json"
        file.write_text(json.dumps(document, ensure_ascii=False))
        argv += ["--input", str(file)]
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = agent_cli.main(argv)
    response = json.loads(stdout.getvalue())
    assert response["status"] == expected, (args, response)
    assert code == (0 if expected == "ok" else 2)
    return response


assert importlib.metadata.metadata("career-scout")["License-Expression"] == "MIT"
checks.append("installed MIT metadata")
ctx = call(
    "context",
    "create",
    "--request-id",
    "context",
    document={
        "resumes": [
            {
                "id": "integration",
                "title": "Специалист интеграций",
                "text": "Интегрировал API. Разрабатывал сервисы на Python.",
            }
        ],
        "preferences": [{"statement": "Только удалённо", "priority": "required"}],
    },
)["result"]["context_id"]
plan = call(
    "plan",
    "put",
    "--context",
    ctx,
    "--request-id",
    "plan",
    document={"sources": ["hh"], "queries": ["интеграции"], "days": 14},
)["result"]["id"]
assert call("plan", "preview", plan)["result"]["network_calls"] == 0
credentials = root / "synthetic.env"
credentials.write_text(
    "HH_ACCESS_TOKEN=synthetic-not-a-credential\nHH_USER_AGENT=OfflineProductAcceptance\n"
)
http_calls = []
texts = {
    "1": ("Инженер интеграций", "Интегрировать API. Работа полностью удалённо."),
    "2": ("Нейрохирург", "Проводить нейрохирургические операции. Только офис."),
    "3": ("Инженер интеграций", "Интегрировать API. Формат работы не указан."),
}


def factory(run, frozen, budget):
    def handle(request):
        http_calls.append(request.url.path)
        if request.url.path == "/vacancies":
            return httpx.Response(
                200,
                json={
                    "items": [{"id": k} for k in texts],
                    "found": 3,
                    "pages": 1,
                    "page": 0,
                    "per_page": 100,
                },
            )
        key = request.url.path.rsplit("/", 1)[-1]
        title, description = texts[key]
        return httpx.Response(
            200,
            json={
                "id": key,
                "name": title,
                "description": "<p>" + description + "</p>",
                "published_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            },
        )

    return HHClient(
        Credentials(credentials),
        client=httpx.Client(transport=httpx.MockTransport(handle)),
        max_requests=budget,
        interval=0,
        retries=0,
    )


agent_service.source_factory = factory
limits = root / "limits.json"
limits.write_text(json.dumps({"max_requests": 2, "chunk_requests": 2}))
run = call(
    "run",
    "start",
    "--context",
    ctx,
    "--plan",
    plan,
    "--limits",
    str(limits),
    "--request-id",
    "start",
)["result"]["run_id"]
partial = call("run", "execute", run, "--request-id", "chunk")
assert partial["result"]["run_status"] == "partial" and len(http_calls) == 2
assert call("run", "execute", run, "--request-id", "chunk") == partial and len(http_calls) == 2
complete = call("run", "resume", run, "--additional-requests", "2", "--request-id", "resume")
assert complete["result"]["run_status"] == "complete" and len(http_calls) == 4
cards = call("run", "results", run, "--full")["result"]
assert cards["total"] == 3 and len({x["source_id"] for x in cards["items"]}) == 3, cards["total"]
checks += ["bounded collection and same-request replay", "resume without duplicate details"]
daily = call(
    "run", "start", "--context", ctx, "--plan", plan, "--mode", "new", "--request-id", "daily"
)["result"]["run_id"]
assert (
    call("run", "execute", daily, "--request-id", "daily-execute")["result"]["run_status"]
    == "complete"
)
assert len(http_calls) == 5 and call("run", "results", daily)["result"]["total"] == 0
checks.append("next collection fetches listings but no old descriptions")

binary = Path("/test-bin/synthetic-codex")
binary.write_text(
    "#!"
    + sys.executable
    + "\n"
    + r'''import json, sys
from pathlib import Path

if sys.argv[1:3] == ["login", "status"]:
    print("Logged in using ChatGPT")
    sys.exit(0)
prompt = sys.stdin.read()
shared = json.loads(prompt.rsplit("\n\n", 1)[1])
results = []
for case in shared["cases"]:
    vid = case["vacancy"]["source_id"]
    evidence = case["vacancy"]["evidence"]

    def ref(term):
        key = next(e["id"] for e in evidence if term in e["text"])
        return {"from": key, "to": key}

    responsibility = ref("операции" if vid == "2" else "Интегрировать API")
    resume = shared["resume"]["evidence"][0]["id"]
    verdict = {"1": "PASS", "2": "REJECT", "3": "UNCERTAIN"}[vid]
    status = "outside_profile" if vid == "2" else "confirmed"
    pref = {
        "preference_id": "pref_1",
        "status": {"1": "met", "2": "violated", "3": "unknown"}[vid],
        "reason": "Synthetic condition check",
        "vacancy_reference": ref("удалённо" if vid == "1" else "офис") if vid != "3" else None,
    }
    results.append(
        {
            "schema_version": 4,
            "policy": "context-1",
            "evaluation_key": case["evaluation_key"],
            "profile_id": shared["profile_id"],
            "verdict": verdict,
            "reason": "Synthetic controlled result",
            "assessments": [
                {
                    "kind": "responsibility",
                    "requirement": "Основная работа",
                    "mandatory": True,
                    "critical": True,
                    "status": status,
                    "explanation": "Synthetic comparison",
                    "vacancy_reference": responsibility,
                    "resume_reference": None if vid == "2" else {"from": resume, "to": resume},
                }
            ],
            "preference_checks": [pref],
            "uncertainties": ["Формат не указан"] if vid == "3" else [],
            "questions": [],
        }
    )
target = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
target.write_text(json.dumps({"results": results}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}}))
'''
)
binary.chmod(0o700)
call(
    "connection",
    "put",
    "default",
    "--request-id",
    "connection",
    document={
        "model": {"executable": str(binary), "max_attempts_per_profile": 1},
        "external_calls_enabled": False,
    },
)
evaluation = call(
    "evaluation",
    "prepare",
    "--context",
    ctx,
    "--collection-run",
    run,
    "--max-calls",
    "1",
    "--max-decisions",
    "0",
    "--request-id",
    "prepare",
)["result"]
eid, db = evaluation["run_id"], evaluation["database"]
assert (
    call("evaluation", "run", eid, "--db", db, "--request-id", "disabled", expected="error")[
        "error"
    ]["code"]
    == "model_calls_disabled"
)
finished = call("evaluation", "run", eid, "--db", db, "--enable-calls", "--request-id", "evaluate")[
    "result"
]
assert finished["status"] == "complete" and finished["outcomes"] == {
    "ACCEPTED": 1,
    "REJECTED_BY_SELECTED": 1,
    "UNCERTAIN": 1,
}, finished
assert finished["calls_reserved"] == 1 and finished["decisions_requests_reserved"] == 0
again = call("evaluation", "resume", eid, "--db", db, "--request-id", "cached")["result"]
assert again["calls_reserved"] == 1
selected = call("evaluation", "shortlist", "--db", db)["result"]
assert selected["total"] == 1 and selected["items"][0]["source_id"] == "1"
with_uncertain = call("evaluation", "shortlist", "--db", db, "--include-uncertain")["result"]
assert with_uncertain["total"] == 2
explained = call("explain", "--analysis-db", db, "--source", "hh", "--id", "1")["result"]
assert explained["evaluations"][0]["verdict"] == "PASS"
checks += [
    "explicit model activation",
    "Codex subprocess/schema/evidence roundtrip with simulator",
    "PASS and UNCERTAIN output separation",
    "cached resume without another model call",
    "explain",
]
event = {
    "operation_id": "applied-one",
    "source": "hh",
    "source_id": "1",
    "kind": "applied",
    "evidence": "synthetic owner import",
    "detail": {},
}
call("history", "import", "--request-id", "history", document=[event])
assert call("evaluation", "shortlist", "--db", db)["result"]["total"] == 0
checks.append("application history removes already applied vacancy")
assert not call("evaluation", "metrics", eid, "--db", db)["result"]["quality"]["measured"]
assert call("evaluation", "status", eid, "--db", db)["result"]["status"] == "complete"
checks.append("unmeasured quality is explicitly marked")
print(
    json.dumps(
        {
            "status": "passed",
            "checks": checks,
            "simulated_job_http_attempts": len(http_calls),
            "simulated_codex_batches": finished["calls_reserved"],
            "real_job_http_attempts": 0,
            "real_model_calls": 0,
            "installed_package": importlib.metadata.version("career-scout"),
            "evidence_kind": "offline installed-package scenario",
        },
        ensure_ascii=False,
    )
)

"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="career-scout:local")
    args = parser.parse_args()
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:mode=1777,size=128m",
            "--tmpfs",
            "/test-bin:exec,nosuid,nodev,mode=0700,size=1m",
            "--entrypoint",
            "/app/.venv/bin/python",
            args.image,
            "-c",
            SCENARIO,
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode:
        raise SystemExit(result.stderr or result.stdout)
    print(result.stdout.strip())


if __name__ == "__main__":
    main()
