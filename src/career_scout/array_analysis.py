"""One manual, resumable pass over an immutable catalog, in owner source order."""

import argparse
import fcntl
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

from career_scout.analysis import AnalysisSettings, prepare, read_catalog, report, run_pending
from career_scout.analysis_store import AnalysisStore
from career_scout.common import digest, utcnow
from career_scout.evaluation import EvaluationError
from career_scout.shortlist import recommendations, render

SOURCE_ORDER = ("hh", "habr", "superjob", "trudvsem")


def freeze_catalog(config, target):
    settings = AnalysisSettings.model_validate_json(config.read_text())
    vacancies, policies = read_catalog(
        (config.parent / settings.collector_database).resolve(), settings.campaign_id
    )
    value = {
        "schema_version": 1,
        "created_at": utcnow(),
        "campaign_id": settings.campaign_id,
        "source_order": list(SOURCE_ORDER),
        "vacancies": vacancies,
        "policies": policies,
    }
    value["content_hash"] = digest(value)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".catalog-", dir=target.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)  # Never replace an existing boundary.
    finally:
        os.unlink(temporary)
    return {"catalog": str(target), "total": len(vacancies), "hash": value["content_hash"]}


def save(path, value):
    """Atomic progress/checkpoint publication, never a half-written JSON file."""
    fd, temporary = tempfile.mkstemp(prefix=".array-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fingerprint(config):
    settings = AnalysisSettings.model_validate_json(config.read_text())
    paths = [
        *settings.profiles.values(),
        *settings.rubrics.values(),
        settings.common_prompt,
        settings.routing.calibration_file,
    ]
    return digest(
        {
            "config": json.loads(config.read_text()),
            "files": {
                p: hashlib.sha256((config.parent / p).read_bytes()).hexdigest() for p in paths
            },
        }
    )


def load_catalog(path):
    catalog = json.loads(path.read_text())
    expected = catalog.pop("content_hash")
    if digest(catalog) != expected or tuple(catalog["source_order"]) != SOURCE_ORDER:
        raise EvaluationError("array_catalog_changed", fatal=True)
    catalog["content_hash"] = expected
    identities = [(v["source"], v["source_id"]) for v in catalog["vacancies"]]
    if len(identities) != len(set(identities)) or any(s not in SOURCE_ORDER for s, _ in identities):
        raise EvaluationError("invalid_array_catalog_identities", fatal=True)
    return catalog


def task_plan(catalog, size):
    tasks = []
    for source in SOURCE_ORDER:
        values = [v for v in catalog["vacancies"] if v["source"] == source]
        for start in range(0, len(values), size):
            tasks.append(
                {"source": source, "start": start, "count": len(values[start : start + size])}
            )
    return tasks


def pending_sources(store, run_id):
    """A previous mixed-source run must not sneak later work into the current phase."""
    sources = set()
    for row in store.db.execute("SELECT * FROM run_jobs WHERE run_id=?", (run_id,)):
        requests = store.inputs(row["route_key"])
        selected = json.loads(row["outcome_json"] or "{}").get("selected_profiles", [])
        if row["status"] == "PENDING" or any(
            r.profile_id in selected and store.cached(r) is None for r in requests
        ):
            sources.add(requests[0].vacancy.source)
    return sources


def publish(store, directory, state):
    """Shortlist grows during the pass; only the final state claims completion."""
    rows = recommendations(store, limit=1_000_000)
    rank = {s: i for i, s in enumerate(SOURCE_ORDER)}
    rows.sort(key=lambda row: rank[row["source"]])
    output = directory / "recommended-vacancies.md"
    fd, temporary = tempfile.mkstemp(prefix=".shortlist-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            if state["status"] == "complete":
                stream.write("Подборка после завершённого прохода.\n\n")
            elif state["status"] == "running":
                stream.write("Подборка пополняется: обработка ещё продолжается.\n\n")
            else:
                stream.write("Подборка неполная: часть вакансий пока не обработана.\n\n")
            stream.write(render(rows))
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    state["shortlist"] = str(output.resolve())
    state["recommended"] = len(rows)
    state["recommended_by_source"] = dict(Counter(r["source"] for r in rows))
    state["updated_at"] = utcnow()
    save(directory / "state.json", state)


def execute(
    config,
    catalog_path,
    directory,
    *,
    chunk_size=100,
    continuous=False,
    stop_after=None,
    shared_slots=False,
):
    """Finite source phases. All repeat invocations resume the same persisted tasks."""
    directory.mkdir(parents=True, exist_ok=True)
    settings = AnalysisSettings.model_validate_json(config.read_text())
    if settings.evaluation_policy != "applications-1" or not settings.routing:
        raise EvaluationError("array_requires_applications_routing", fatal=True)
    if not 1 <= chunk_size <= settings.routing.max_requests:
        raise EvaluationError("invalid_array_chunk_budget", fatal=True)
    database = (config.parent / settings.database).resolve()
    with (directory / "coordinator.lock").open("a") as coordinator:
        fcntl.flock(coordinator, fcntl.LOCK_EX | fcntl.LOCK_NB)
        catalog = load_catalog(catalog_path)
        if catalog["campaign_id"] != settings.campaign_id:
            raise EvaluationError("array_campaign_mismatch", fatal=True)
        identity = {
            "catalog_hash": catalog["content_hash"],
            "config_fingerprint": fingerprint(config),
            "chunk_size": chunk_size,
        }
        checkpoint = directory / "state.json"
        state = (
            json.loads(checkpoint.read_text())
            if checkpoint.exists()
            else {
                **identity,
                "created_at": utcnow(),
                "source_order": list(SOURCE_ORDER),
                "total": len(catalog["vacancies"]),
                "by_source": dict(Counter(v["source"] for v in catalog["vacancies"])),
                "tasks": task_plan(catalog, chunk_size),
            }
        )
        if any(state.get(k) != v for k, v in identity.items()):
            raise EvaluationError("array_configuration_changed", fatal=True)
        if stop_after is not None:
            if stop_after < 1:
                raise EvaluationError("invalid_array_stop_limit", fatal=True)
            state["stop_after"] = stop_after
        if continuous:
            return execute_continuous(
                config, catalog, directory, state, database, shared_slots=shared_slots
            )
        if shared_slots:
            raise EvaluationError("shared_slots_requires_continuous_mode", fatal=True)
        if state.get("stop_after") is not None:
            raise EvaluationError("stop_limit_requires_continuous_mode", fatal=True)
        state["status"] = "running"
        state.pop("error", None)
        save(checkpoint, state)
        for index, task in enumerate(state["tasks"]):
            if task.get("status") in {"complete", "incomplete_evaluations"}:
                continue
            state["active_task"] = index
            state["active_source"] = task["source"]
            state["updated_at"] = utcnow()
            save(checkpoint, state)
            if "runs" not in task:
                source = [v for v in catalog["vacancies"] if v["source"] == task["source"]]
                values = source[task["start"] : task["start"] + task["count"]]
                prepared = prepare(
                    config,
                    limit=len(values),
                    # Worst case two profiles, one-item context splits, two attempts.
                    max_calls=4 * len(values),
                    catalog=(values, catalog["policies"]),
                )
                task["runs"] = [*prepared["reused_runs"], prepared["run_id"]]
                task["new_jobs"] = prepared["jobs"]
                task["reused_jobs"] = prepared["previously_admitted_skipped"]
                save(checkpoint, state)
            store = AnalysisStore(database)
            try:
                reports = []
                for run_id in task["runs"]:
                    current = report(store, run_id)
                    if current["status"] != "complete":
                        sources = pending_sources(store, run_id)
                        if sources - {task["source"]}:
                            state.update(status="blocked", error="pending_run_crosses_source_phase")
                            publish(store, directory, state)
                            return state
                        # Older zero-budget prepared tasks are authorized by this full pass.
                        calls = max(0, 4 * current["jobs"] - current["max_calls"])
                        with Path(str(database) + ".lock").open("a") as lock:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            current = run_pending(store, run_id, additional_calls=calls)
                    reports.append(current)
                    if current.get("error"):
                        task["reports"] = reports
                        state.update(status="blocked", error=current["error"])
                        publish(store, directory, state)
                        return state
                task["reports"] = reports
                task["status"] = (
                    "complete"
                    if all(r["status"] == "complete" for r in reports)
                    else "incomplete_evaluations"
                )
                publish(store, directory, state)
            finally:
                store.close()
            print(
                json.dumps(
                    {
                        "task": index + 1,
                        "source": task["source"],
                        "status": task["status"],
                        "recommended": state["recommended"],
                    }
                ),
                flush=True,
            )
        state["status"] = (
            "complete"
            if all(t["status"] == "complete" for t in state["tasks"])
            else "complete_with_errors"
        )
        state.pop("active_task", None)
        state.pop("active_source", None)
        state["finished_at"] = utcnow()
        store = AnalysisStore(database)
        try:
            publish(store, directory, state)
        finally:
            store.close()
        return state


def apply_stop_limit(state):
    """Split only unadmitted portions; never dispatch a card beyond the boundary."""
    limit = state.get("stop_after", state["total"])
    ordinal = 0
    for index, task in enumerate(state["tasks"]):
        end = ordinal + task["count"]
        if ordinal < limit < end:
            if "runs" in task:
                raise EvaluationError("stop_limit_crosses_admitted_task", fatal=True)
            first = limit - ordinal
            state["tasks"][index : index + 1] = [
                {**task, "count": first},
                {
                    "source": task["source"],
                    "start": task["start"] + first,
                    "count": task["count"] - first,
                },
            ]
            break
        if ordinal >= limit and "runs" in task:
            raise EvaluationError("stop_limit_below_admitted_work", fatal=True)
        ordinal = end
    # Check the suffix too after a split.
    ordinal = 0
    for task in state["tasks"]:
        if ordinal >= limit and "runs" in task:
            raise EvaluationError("stop_limit_below_admitted_work", fatal=True)
        ordinal += task["count"]
    return limit


def execute_continuous(config, catalog, directory, state, database, *, shared_slots=False):
    """Refill one shared profile pool, retaining source barriers and persisted runs."""
    from career_scout.decisions import DecisionsClient
    from career_scout.routed_pipeline import (
        aggregate,
        evaluate_queues,
        initialize,
        revalidate_saved_responses,
        route_jobs,
    )

    limit = apply_stop_limit(state)
    checkpoint = directory / "state.json"
    state.update(
        status="running",
        execution_mode="continuous",
        updated_at=utcnow(),
        shared_worker_slots=shared_slots,
    )
    state.pop("error", None)
    state.setdefault("continuous_started_at", utcnow())
    save(checkpoint, state)
    store = AnalysisStore(database)
    initialize(store)
    ordinal = 0
    eligible = []
    for index, task in enumerate(state["tasks"]):
        if ordinal < limit:
            eligible.append((index, task))
        ordinal += task["count"]
    try:
        with Path(str(database) + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for source in SOURCE_ORDER:
                tasks = [
                    (i, t)
                    for i, t in eligible
                    if t["source"] == source
                    and t.get("status") not in {"complete", "incomplete_evaluations"}
                ]
                if not tasks:
                    continue
                pending = iter(tasks)
                active_runs = []
                frozen = None
                state["active_source"] = source

                def progress():
                    state["updated_at"] = utcnow()
                    save(checkpoint, state)

                def refill(pending=pending, source=source, active_runs=active_runs):
                    nonlocal frozen
                    item = next(pending, None)
                    if item is None:
                        return None
                    index, task = item
                    state["active_task"] = index
                    progress()
                    if "runs" not in task:
                        values = [v for v in catalog["vacancies"] if v["source"] == source]
                        values = values[task["start"] : task["start"] + task["count"]]
                        prepared = prepare(
                            config,
                            limit=len(values),
                            max_calls=4 * len(values),
                            catalog=(values, catalog["policies"]),
                            coordinator_lock=lock,
                        )
                        task["runs"] = [*prepared["reused_runs"], prepared["run_id"]]
                        task["new_jobs"] = prepared["jobs"]
                        task["reused_jobs"] = prepared["previously_admitted_skipped"]
                        save(checkpoint, state)
                    ready = []
                    for run_id in task["runs"]:
                        current = report(store, run_id)
                        if current["status"] == "complete":
                            continue
                        if pending_sources(store, run_id) - {source}:
                            raise EvaluationError("pending_run_crosses_source_phase", fatal=True)
                        value = json.loads(store.run(run_id)["config_json"])
                        if not value["external_calls_enabled"]:
                            raise EvaluationError("model_calls_disabled", fatal=True)
                        if frozen is None:
                            frozen = value
                        if value["model"] != frozen["model"]:
                            raise EvaluationError("continuous_model_changed", fatal=True)
                        extra = max(0, 4 * current["jobs"] - current["max_calls"])
                        if extra:
                            with store.db:
                                store.db.execute(
                                    "UPDATE analysis_runs SET max_calls=max_calls+? WHERE id=?",
                                    (extra, run_id),
                                )
                        revalidate_saved_responses(store, run_id)
                        try:
                            route_jobs(
                                store,
                                run_id,
                                value,
                                DecisionsClient(
                                    Path(value["routing"]["workspace"]),
                                    max_attempts=value["routing"].get("native_max_attempts", 1),
                                ),
                            )
                        finally:
                            aggregate(store, run_id)
                        if run_id not in active_runs:
                            active_runs.append(run_id)
                            ready.append(run_id)
                    progress()
                    return ready

                error = None
                try:
                    initial = refill()
                    # Entire portions can consist of earlier exclusions/cache.
                    while initial is not None and not initial:
                        initial = refill()
                    if initial:
                        error = evaluate_queues(
                            store,
                            initial,
                            frozen,
                            refill=refill,
                            on_progress=progress,
                            shared_slots=shared_slots,
                        )
                except EvaluationError as exc:
                    error = exc.code
                for _, task in tasks:
                    if "runs" not in task:
                        continue
                    task["reports"] = [report(store, r) for r in task["runs"]]
                    if all(r["status"] == "complete" for r in task["reports"]):
                        task["status"] = "complete"
                    elif not error:
                        task["status"] = "incomplete_evaluations"
                if error:
                    state.update(status="blocked", error=error)
                    publish(store, directory, state)
                    return state
                publish(store, directory, state)
            complete = all(t.get("status") == "complete" for _, t in eligible)
            state["status"] = (
                "stopped_at_limit"
                if complete and limit < state["total"]
                else "complete"
                if complete
                else "complete_with_errors"
            )
            state["completed_catalog_count"] = sum(
                t["count"] for _, t in eligible if t.get("status") == "complete"
            )
            state["finished_at"] = utcnow()
            state.pop("active_task", None)
            state.pop("active_source", None)
            publish(store, directory, state)
            return state
    finally:
        store.close()


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--freeze", action="store_true", help="Only freeze an exclusive catalog")
    parser.add_argument("--chunk-size", type=int, default=100)
    parser.add_argument(
        "--continuous", action="store_true", help="Keep four queues fed across portions"
    )
    parser.add_argument(
        "--stop-after", type=int, help="Stop at this catalog ordinal, including prior work"
    )
    parser.add_argument(
        "--share-worker-slots",
        action="store_true",
        help="Lend idle slots to other profile queues, at most four calls total",
    )
    args = parser.parse_args(argv)
    if not args.freeze and args.output is None:
        parser.error("--output is required for execution")
    try:
        if args.freeze:
            print(json.dumps(freeze_catalog(args.config.resolve(), args.catalog.resolve())))
            return 0
        state = execute(
            args.config.resolve(),
            args.catalog.resolve(),
            args.output.resolve(),
            chunk_size=args.chunk_size,
            continuous=args.continuous,
            stop_after=args.stop_after,
            shared_slots=args.share_worker_slots,
        )
        print(json.dumps({k: v for k, v in state.items() if k != "tasks"}, ensure_ascii=False))
        return 0 if state["status"] in {"complete", "stopped_at_limit"} else 2
    except EvaluationError as exc:
        print(json.dumps({"error": exc.code}), flush=True)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
