"""Opt-in, sequential Codex sanity checks against labels frozen before inference."""

import argparse
import html
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from career_scout.analysis import AnalysisSettings
from career_scout.analysis_store import AnalysisStore
from career_scout.application import parse_result
from career_scout.codex_worker import CodexWorker
from career_scout.common import digest
from career_scout.evaluation import (
    EvaluationError,
    ResumeSnapshot,
    make_input,
    validate_result,
)
from career_scout.hh import normalize
from career_scout.models import Vacancy
from career_scout.normalization import project_snapshot


def prepare(config_path, cases_path, output, max_calls):
    settings = AnalysisSettings.model_validate_json(config_path.read_text())
    if not settings.external_calls_enabled or not settings.order_confirmed:
        raise EvaluationError("model_calls_not_authorized", fatal=True)
    cases = json.loads(cases_path.read_text())["cases"]
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("duplicate_case_id")
    if any(c["expected"] not in {"PASS", "BORDERLINE", "REJECT"} for c in cases):
        raise ValueError("invalid_gold_label")
    root = config_path.resolve().parent
    requests = []
    for index, case in enumerate(cases):
        role = case["profile_id"]
        raw = {
            "id": str(index + 1),
            "name": case["title"],
            "description": "<p>"
            + html.escape(case["description"]).replace("\n", "</p><p>")
            + "</p>",
            "employer": {"id": "synthetic-quality", "name": "Synthetic quality test"},
            "experience": {"id": "between1And3"},
        }
        requests.append(
            make_input(
                vacancy=Vacancy.model_validate(project_snapshot(normalize(raw, raw["id"]))),
                resume=ResumeSnapshot.model_validate_json(
                    (root / settings.profiles[role]).read_text()
                ),
                filters={
                    "owner_attributes": settings.owner_attributes,
                    **(
                        {"evaluation_policy": settings.evaluation_policy}
                        if settings.evaluation_policy != "strict-1"
                        else {}
                    ),
                },
                filter_version="quality-sanity-1",
                rubric=(root / settings.rubrics[role]).read_text(),
                rubric_version=settings.rubric_version,
                common_instructions=(root / settings.common_prompt).read_text(),
                config=settings.model,
                evaluation_date=datetime.now(UTC),
            )
        )
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    store = AnalysisStore(output / "analysis.sqlite3")
    try:
        rid = store.create_run({"model": settings.model.model_dump(mode="json")}, max_calls)
        for request in requests:
            store.save_input(request)
        manifest = {
            "run_id": rid,
            "model": settings.model.model_dump(mode="json"),
            "kind": "synthetic_sanity_with_private_resumes",
            "cases": [
                {**case, "key": request.evaluation_key}
                for case, request in zip(cases, requests, strict=True)
            ],
        }
        manifest["gold_hash"] = digest(manifest["cases"])
        (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    finally:
        store.close()


def execute(output, worker_factory=CodexWorker):
    from career_scout.evaluation import EvaluatorConfig

    manifest = json.loads((output / "manifest.json").read_text())
    if digest(manifest["cases"]) != manifest["gold_hash"]:
        raise ValueError("gold_manifest_changed")
    store = AnalysisStore(output / "analysis.sqlite3")
    worker = worker_factory(EvaluatorConfig.model_validate(manifest["model"]))
    rows = []
    try:
        for case in manifest["cases"]:
            request = store.request(case["key"])
            value = store.cached(request)
            error = None
            # One attempt per case in this baseline, including after interruption.
            if value is None and store.attempt_count(manifest["run_id"], case["key"]) == 0:
                attempt = store.reserve(manifest["run_id"], case["key"])
                reply = None
                fatal = False
                failed_usage = None
                failed_response = None
                try:
                    reply = worker(request)
                    value = validate_result(request, parse_result(reply.result))
                    value = store.save_result(request, value)
                except (EvaluationError, ValidationError) as exc:
                    error = exc.code if isinstance(exc, EvaluationError) else "invalid_result"
                    fatal = isinstance(exc, EvaluationError) and exc.fatal
                    failed_usage = getattr(exc, "usage", None)
                    failed_response = getattr(exc, "response", None)
                store.finish_attempt(
                    attempt,
                    error=error,
                    usage=reply.usage if reply else failed_usage,
                    response=reply.audit_response() if reply else failed_response,
                )
                if fatal:
                    raise EvaluationError(error, fatal=True)
            actual = value.verdict if value else "ERROR"
            if value is None and error is None:
                prior = store.db.execute(
                    "SELECT error FROM attempts WHERE run_id=? AND evaluation_key=? "
                    "ORDER BY id DESC LIMIT 1",
                    (manifest["run_id"], case["key"]),
                ).fetchone()
                error = (prior[0] or "attempt_interrupted") if prior else "not_run"
            row = {
                "id": case["id"],
                "profile_id": case["profile_id"],
                "expected": case["expected"],
                "actual": actual,
                "label_match": actual == case["expected"],
                "result": value.model_dump(mode="json") if value else None,
                "error": error,
            }
            rows.append(row)
            (output / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
            print(json.dumps({k: v for k, v in row.items() if k != "result"}), flush=True)
        return rows
    finally:
        store.close()


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--cases", type=Path, default=Path("tests/fixtures/evaluation-quality.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=0)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not args.resume:
        if args.config is None:
            parser.error("--config required for prepare")
        prepare(args.config, args.cases, args.output, args.max_calls)
    if args.execute:
        rows = execute(args.output)
        raise SystemExit(0 if all(r["label_match"] for r in rows) else 2)
