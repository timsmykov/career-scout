"""Manual prepare/run/resume/export for the Codex-backed analysis service."""

import argparse
import fcntl
import json
import os
import sqlite3
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field, ValidationError, model_validator

from career_scout.analysis_graph import AnalysisGraph
from career_scout.analysis_store import AnalysisStore
from career_scout.campaigns import Campaigns
from career_scout.codex_worker import CodexWorker
from career_scout.evaluation import (
    ORDER,
    EvaluationError,
    EvaluatorConfig,
    ProfileID,
    ResumeSnapshot,
    make_input,
)
from career_scout.models import Model, Vacancy
from career_scout.storage import Store


class AnalysisSettings(Model):
    schema_version: int = 1
    collector_database: str
    campaign_id: str
    database: str
    profiles: dict[ProfileID, str]
    rubrics: dict[ProfileID, str]
    common_prompt: str
    rubric_version: str
    order: list[ProfileID] = Field(default_factory=lambda: list(ORDER))
    order_confirmed: bool = False
    external_calls_enabled: bool = False
    owner_attributes: dict = Field(default_factory=dict)
    model: EvaluatorConfig = Field(default_factory=EvaluatorConfig)

    @model_validator(mode="after")
    def validate_profiles(self):
        if set(self.profiles) != set(ORDER) or set(self.rubrics) != set(ORDER):
            raise ValueError("four_profiles_required")
        if len(self.order) != 4 or set(self.order) != set(ORDER):
            raise ValueError("invalid_profile_order")
        return self


def read_catalog(path: Path, campaign_id):
    # Never construct Store normally on the producer's DB: analysis does not
    # migrate it, alter its journal settings, or acquire its writer lock.
    store = Store.__new__(Store)
    store.path = path
    store.db = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    store.db.row_factory = sqlite3.Row
    try:
        campaigns = Campaigns(store)
        catalog = campaigns.export(campaign_id)
        policies = {
            r["source"]: dict(store.active_policy(r["id"])) for r in campaigns.runs(campaign_id)
        }
        decisions = {
            (d["source"], d["source_id"]): d["decision"]["status"]
            for d in catalog["filter_decisions"]
        }
        # Confirmed deterministic eligibility is processed first. Unknowns remain
        # in the queue; this priority does not reject them or assert role fit.
        vacancies = sorted(
            catalog["vacancies"],
            key=lambda v: (
                decisions[(v["source"], v["source_id"])] != "KEEP",
                v["source"],
                v["source_id"],
            ),
        )
        return vacancies, policies
    finally:
        store.db.close()


def prepare(config_path: Path, *, limit, max_calls):
    settings = AnalysisSettings.model_validate_json(config_path.read_text())
    parent = config_path.resolve().parent

    def resolve(value):
        return (parent / value).resolve()

    if resolve(settings.database) == resolve(settings.collector_database):
        raise EvaluationError("analysis_requires_separate_database", fatal=True)
    resumes = {
        p: ResumeSnapshot.model_validate_json(resolve(path).read_text())
        for p, path in settings.profiles.items()
    }
    if any(resumes[p].profile_id != p for p in ORDER):
        raise EvaluationError("resume_profile_mismatch", fatal=True)
    common = resolve(settings.common_prompt).read_text()
    rubrics = {p: resolve(path).read_text() for p, path in settings.rubrics.items()}
    vacancies, policies = read_catalog(resolve(settings.collector_database), settings.campaign_id)
    prepared_at = datetime.now(UTC)
    store = AnalysisStore(resolve(settings.database))
    try:
        frozen = {
            "model": settings.model.model_dump(mode="json"),
            "order": settings.order,
            "order_confirmed": settings.order_confirmed,
            "external_calls_enabled": settings.external_calls_enabled,
            "source_campaign_id": settings.campaign_id,
        }
        rid = store.create_run(frozen, max_calls)
        for vacancy in vacancies[:limit]:
            source = vacancy["source"]
            policy = policies[source]
            filters = {
                "eligibility": json.loads(policy["policy_json"]),
                "owner_attributes": settings.owner_attributes,
            }
            requests = [
                make_input(
                    vacancy=Vacancy.model_validate(vacancy),
                    resume=resumes[p],
                    filters=filters,
                    filter_version=policy["policy_hash"],
                    rubric=rubrics[p],
                    rubric_version=settings.rubric_version,
                    common_instructions=common,
                    config=settings.model,
                    evaluation_date=prepared_at,
                )
                for p in settings.order
            ]
            store.add_job(rid, requests)
        return report(store, rid)
    finally:
        store.close()


def report(store, run_id):
    run = store.run(run_id)
    jobs = list(
        store.db.execute(
            "SELECT route_key,status FROM run_jobs WHERE run_id=? ORDER BY position", (run_id,)
        )
    )
    states = Counter(row["status"] for row in jobs)
    usages = [
        json.loads(r[0])
        for r in store.db.execute(
            "SELECT usage_json FROM attempts WHERE run_id=? AND usage_json IS NOT NULL", (run_id,)
        )
    ]
    return {
        "run_id": run_id,
        "database": str(store.path),
        "status": "partial" if states["PENDING"] else "complete",
        "jobs": len(jobs),
        "outcomes": dict(states),
        "calls_reserved": run["calls"],
        "max_calls": run["max_calls"],
        "attempts_with_usage": len(usages),
        "measured_tokens": {
            key: sum(u.get(key, 0) for u in usages)
            for key in ("input_tokens", "cached_input_tokens", "output_tokens")
        }
        if usages
        else None,
        "billing": "codex_subscription",
        "cost_usd": None,
    }


def run_pending(store, run_id, *, additional_calls=0, retry_errors=False):
    if retry_errors or additional_calls:
        store.retry(run_id, additional_calls)
    frozen = json.loads(store.run(run_id)["config_json"])
    if not frozen["external_calls_enabled"]:
        raise EvaluationError("model_calls_disabled", fatal=True)
    config = EvaluatorConfig.model_validate(frozen["model"])
    graph = AnalysisGraph(
        store,
        CodexWorker(config),
        config,
        order=frozen["order"],
        order_confirmed=frozen["order_confirmed"],
    )
    error = None
    jobs = list(
        store.db.execute(
            "SELECT route_key FROM run_jobs WHERE run_id=? AND status='PENDING' ORDER BY position",
            (run_id,),
        )
    )
    for job in jobs:
        try:
            graph.evaluate(run_id, job["route_key"])
        except EvaluationError as exc:
            error = exc.code
            break
    result = report(store, run_id)
    if error:
        result["error"] = error
    return result


def export_results(store, run_id, path: Path):
    result = {"report": report(store, run_id), "vacancies": []}
    for job in store.db.execute(
        "SELECT * FROM run_jobs WHERE run_id=? ORDER BY position", (run_id,)
    ):
        requests = store.inputs(job["route_key"])
        vacancy = requests[0].vacancy
        results = {
            r.profile_id: cached.model_dump(mode="json")
            for r in requests
            if (cached := store.cached(r)) is not None
        }
        result["vacancies"].append(
            {
                "source": vacancy.source,
                "source_id": vacancy.source_id,
                "url": vacancy.url,
                "title": vacancy.title,
                "route_key": job["route_key"],
                "status": job["status"],
                "routing": json.loads(job["outcome_json"]) if job["outcome_json"] else None,
                "evaluations": results,
                "resume_versions": {
                    r.profile_id: {
                        "resume_id": r.resume.resume_id,
                        "content_hash": r.resume.content_hash,
                        "version": r.resume.version,
                    }
                    for r in requests
                },
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".analysis-export-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        os.unlink(temporary)
    return {"output": str(path.resolve()), "vacancies": len(result["vacancies"])}


def positive(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser(
        "prepare", help="Freeze full vacancy/resume inputs without model calls"
    )
    command.add_argument("--config", type=Path, required=True)
    command.add_argument("--limit", type=positive, default=20)
    command.add_argument("--max-calls", type=int, default=0)
    for name in ("run", "resume", "status", "export"):
        command = commands.add_parser(name)
        command.add_argument("run_id")
        command.add_argument("--db", type=Path, required=True)
        if name in ("run", "resume"):
            command.add_argument("--additional-calls", type=int, default=0)
            command.add_argument("--retry-errors", action="store_true")
        if name == "export":
            command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    store = None
    try:
        if args.command == "prepare":
            result = prepare(args.config, limit=args.limit, max_calls=args.max_calls)
        else:
            store = AnalysisStore(args.db)
            if args.command in ("run", "resume"):
                with Path(str(args.db) + ".lock").open("a") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    result = run_pending(
                        store,
                        args.run_id,
                        additional_calls=args.additional_calls,
                        retry_errors=args.retry_errors,
                    )
            elif args.command == "status":
                result = report(store, args.run_id)
            else:
                result = export_results(store, args.run_id, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("status") == "partial" and args.command != "prepare" else 0
    except EvaluationError as exc:
        print(json.dumps({"error": exc.code}))
        return 3
    except (OSError, sqlite3.Error, ValidationError, ValueError):
        print(json.dumps({"error": "analysis_config_or_storage_error"}))
        return 3
    except KeyboardInterrupt:
        print(json.dumps({"error": "analysis_interrupted"}))
        return 130
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
