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
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from career_scout import exclusions, intake
from career_scout.analysis_graph import AnalysisGraph
from career_scout.analysis_store import AnalysisStore
from career_scout.campaigns import Campaigns
from career_scout.codex_worker import CodexWorker
from career_scout.common import digest
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


class RoutingSettings(Model):
    calibration_file: str
    workspace: str
    max_requests: int = Field(default=0, ge=0)
    batch_size: Literal[10, 20] = 10
    max_batch_characters: int | None = Field(default=None, ge=100, le=2_000_000)
    prescreen_policy_hash: str | None = None
    fit_screen_policy_hash: str | None = None
    native_max_attempts: int = Field(default=1, ge=1, le=3)
    workers: int = Field(default=4, ge=4, le=4)


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
    routing: RoutingSettings | None = None
    evaluation_policy: Literal["strict-1", "applications-1"] = "strict-1"

    @model_validator(mode="after")
    def validate_profiles(self):
        if set(self.profiles) != set(ORDER) or set(self.rubrics) != set(ORDER):
            raise ValueError("four_profiles_required")
        if len(self.order) != 4 or set(self.order) != set(ORDER):
            raise ValueError("invalid_profile_order")
        if self.evaluation_policy == "applications-1" and not self.routing:
            raise ValueError("application_policy_requires_routed_mode")
        if (
            self.routing
            and self.routing.fit_screen_policy_hash
            and not self.routing.prescreen_policy_hash
        ):
            raise ValueError("fit_screen_requires_prescreen")
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


def prepare(
    config_path: Path, *, limit, max_calls, reprocess=False, catalog=None, coordinator_lock=None
):
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
    routing = None
    if settings.routing:
        from career_scout.decisions import INSTRUCTIONS, MODEL, PROFILES

        calibration = json.loads(resolve(settings.routing.calibration_file).read_text())
        expected = digest({"model": MODEL, "instructions": INSTRUCTIONS, "profiles": PROFILES})
        margin = calibration.get("margin")
        if (
            calibration.get("policy_hash") != expected
            or type(margin) not in (int, float)
            or not 0 <= margin <= 1
        ):
            raise EvaluationError("invalid_routing_calibration", fatal=True)
        routing = {
            **settings.routing.model_dump(),
            "margin": margin,
            "workspace": str(resolve(settings.routing.workspace)),
            "calibration_hash": digest(calibration),
            "policy_hash": expected,
        }
    vacancies, policies = (
        catalog
        if catalog is not None
        else read_catalog(resolve(settings.collector_database), settings.campaign_id)
    )
    if routing and routing.get("prescreen_policy_hash"):
        from career_scout.prescreen import POLICY_HASH

        if routing["prescreen_policy_hash"] != POLICY_HASH or any(
            json.loads(p["policy_json"]).get("max_required_years") != 3
            or set(json.loads(p["policy_json"]).get("allowed_grades", []))
            != {"intern", "junior", "middle"}
            for p in policies.values()
        ):
            raise EvaluationError("incompatible_prescreen_policy", fatal=True)
    if routing and routing.get("fit_screen_policy_hash"):
        from career_scout.fit_screen import POLICY_HASH

        if routing["fit_screen_policy_hash"] != POLICY_HASH:
            raise EvaluationError("incompatible_fit_screen_policy", fatal=True)
    prepared_at = datetime.now(UTC)
    store = AnalysisStore(resolve(settings.database))
    lock_path = Path(str(store.path) + ".lock")
    if coordinator_lock is not None and Path(coordinator_lock.name).resolve() != lock_path:
        store.close()
        raise EvaluationError("analysis_lock_mismatch", fatal=True)
    lock = coordinator_lock if coordinator_lock is not None else lock_path.open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        intake.initialize(store)
        # Synthetic/offline catalog callers may have no collector; live prepares do.
        if resolve(settings.collector_database).exists():
            exclusions.sync_collector(store, resolve(settings.collector_database), policies)
        frozen = {
            "model": settings.model.model_dump(mode="json"),
            "order": settings.order,
            "order_confirmed": settings.order_confirmed,
            "external_calls_enabled": settings.external_calls_enabled,
            "source_campaign_id": settings.campaign_id,
            "evaluation_policy": settings.evaluation_policy,
        }
        if routing:
            frozen["routing"] = routing
        frozen["selection_key"] = digest(
            {
                "resumes": {p: r.model_dump(mode="json") for p, r in resumes.items()},
                "rubrics": rubrics,
                "common": common,
                "attributes": settings.owner_attributes,
                "model": frozen["model"],
                "evaluation_policy": settings.evaluation_policy,
                "filters": {s: p["policy_hash"] for s, p in policies.items()},
                "route": {"policy_hash": routing["policy_hash"], "margin": routing["margin"]}
                if routing
                else settings.order,
            }
        )
        rid = store.create_run(frozen, max_calls)
        skipped = 0
        excluded = 0
        reused_runs = set()
        for vacancy in vacancies:
            if (
                store.db.execute("SELECT COUNT(*) FROM run_jobs WHERE run_id=?", (rid,)).fetchone()[
                    0
                ]
                >= limit
            ):
                break
            source = vacancy["source"]
            if exclusions.identity_blocked(store.db, source, vacancy["source_id"]):
                excluded += 1
                continue
            policy = policies[source]
            filters = {
                "eligibility": json.loads(policy["policy_json"]),
                "owner_attributes": settings.owner_attributes,
            }
            if settings.evaluation_policy != "strict-1":
                filters["evaluation_policy"] = settings.evaluation_policy
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
            policy_name = "decisions-one-two-1" if routing else "first-pass-1"
            key = intake.route_key(requests, policy_name)
            if not reprocess and intake.admitted(store, frozen, key):
                skipped += 1
                reused_runs.add(intake.admission_run(store, frozen, key))
                continue
            store.add_job(rid, requests, policy=policy_name)
            if not intake.admitted(store, frozen, key):
                intake.record(store, frozen, key, rid)
        result = report(store, rid)
        result["previously_admitted_skipped"] = skipped
        result["permanently_excluded_skipped"] = excluded
        result["reused_runs"] = sorted(reused_runs)
        result["pending_previous_runs"] = [
            r for r in intake.pending_runs(store, frozen) if r != rid
        ]
        return result
    finally:
        if coordinator_lock is None:
            lock.close()
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
    batches = []
    incomplete_selected = 0
    if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='batch_attempts'").fetchone():
        batches = store.db.execute(
            "SELECT * FROM batch_attempts WHERE run_id=?", (run_id,)
        ).fetchall()
        usages.extend(json.loads(r["usage_json"]) for r in batches if r["usage_json"])
        for selection in store.db.execute(
            "SELECT s.route_key,s.selected_json FROM routed_selections s JOIN run_jobs j "
            "ON j.run_id=s.run_id AND j.route_key=s.route_key "
            "WHERE s.run_id=? AND j.status!='EXCLUDED'",
            (run_id,),
        ):
            selected = json.loads(selection["selected_json"])
            if not selected:
                continue
            incomplete_selected += sum(
                r.profile_id in selected and store.cached(r) is None
                for r in store.inputs(selection["route_key"])
            )
    return {
        "run_id": run_id,
        "database": str(store.path),
        "status": "partial" if states["PENDING"] or incomplete_selected else "complete",
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
        "batch_calls": len(batches),
        "batch_items": sum(len(json.loads(b["keys_json"])) for b in batches),
        "incomplete_selected_evaluations": incomplete_selected,
    }


def run_pending(store, run_id, *, additional_calls=0, retry_errors=False):
    exclusions.apply_run(store, run_id)
    if retry_errors or additional_calls:
        store.retry(run_id, additional_calls)
    frozen = json.loads(store.run(run_id)["config_json"])
    if not frozen["external_calls_enabled"]:
        raise EvaluationError("model_calls_disabled", fatal=True)
    config = EvaluatorConfig.model_validate(frozen["model"])
    if frozen.get("routing"):
        from career_scout.routed_pipeline import run_routed

        error = run_routed(store, run_id, frozen)
        result = report(store, run_id)
        if error:
            result["error"] = error
        return result
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
    routed = bool(json.loads(store.run(run_id)["config_json"]).get("routing"))
    selections = {}
    if (
        routed
        and store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='routed_selections'"
        ).fetchone()
    ):
        selections = {
            r["route_key"]: json.loads(r["selected_json"])
            for r in store.db.execute(
                "SELECT route_key,selected_json FROM routed_selections WHERE run_id=?", (run_id,)
            )
        }
    for job in store.db.execute(
        "SELECT * FROM run_jobs WHERE run_id=? ORDER BY position", (run_id,)
    ):
        requests = store.inputs(job["route_key"])
        vacancy = requests[0].vacancy
        results = {
            r.profile_id: cached.model_dump(mode="json")
            for r in requests
            if (not routed or r.profile_id in selections.get(job["route_key"], []))
            and (cached := store.cached(r)) is not None
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
    command.add_argument(
        "--reprocess", action="store_true", help="Explicitly prepare previous inputs again"
    )
    shortlist = commands.add_parser("shortlist", help="Only application recommendations")
    shortlist.add_argument("--db", type=Path, required=True)
    shortlist.add_argument("--run-id")
    shortlist.add_argument("--profile", choices=list(ORDER))
    shortlist.add_argument("--limit", type=positive, default=20)
    shortlist.add_argument("--output", type=Path)
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
            result = prepare(
                args.config, limit=args.limit, max_calls=args.max_calls, reprocess=args.reprocess
            )
        else:
            store = AnalysisStore(args.db)
            if args.command == "shortlist":
                from career_scout.shortlist import recommendations, render

                content = render(
                    recommendations(
                        store, run_id=args.run_id, profile=args.profile, limit=args.limit
                    )
                )
                if args.output:
                    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "w") as stream:
                        stream.write(content)
                print(content)
                return 0
            elif args.command in ("run", "resume"):
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
