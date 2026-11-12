"""Four profile queues. Model threads never share a SQLite connection."""

import json
import uuid
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from pydantic import ValidationError

from career_scout.application import parse_result
from career_scout.batch_worker import CodexBatchWorker, batch_size_characters
from career_scout.common import utcnow
from career_scout.decisions import DecisionsClient, select_profiles
from career_scout.evaluation import ORDER, EvaluationError, EvaluatorConfig, validate_result
from career_scout.evidence import hydrate_response

SCHEMA = """
CREATE TABLE IF NOT EXISTS routed_selections(
 run_id TEXT NOT NULL,route_key TEXT NOT NULL,decision_json TEXT NOT NULL,
 selected_json TEXT NOT NULL,
 PRIMARY KEY(run_id,route_key),FOREIGN KEY(run_id,route_key) REFERENCES run_jobs(run_id,route_key));
CREATE TABLE IF NOT EXISTS routing_budgets(run_id TEXT PRIMARY KEY,max_requests INTEGER NOT NULL,
 requests INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS batch_attempts(
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES analysis_runs(id),profile TEXT NOT NULL,
 keys_json TEXT NOT NULL,generation INTEGER NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,
 usage_json TEXT,response_json TEXT,error TEXT);
CREATE TABLE IF NOT EXISTS batch_items(
 batch_id TEXT NOT NULL REFERENCES batch_attempts(id),key TEXT NOT NULL REFERENCES inputs(key),
 error TEXT,PRIMARY KEY(batch_id,key));
CREATE TABLE IF NOT EXISTS routed_input_errors(
 run_id TEXT NOT NULL,key TEXT NOT NULL,error TEXT NOT NULL,PRIMARY KEY(run_id,key));
CREATE TABLE IF NOT EXISTS validation_recoveries(
 batch_id TEXT NOT NULL REFERENCES batch_attempts(id),key TEXT NOT NULL REFERENCES inputs(key),
 validator_version TEXT NOT NULL,recovered_at TEXT NOT NULL,
 PRIMARY KEY(batch_id,key,validator_version));
"""


def initialize(store):
    store.db.executescript(SCHEMA)


def revalidate_saved_responses(store, run_id):
    """Replay unchanged wire results after validator fixes, without model calls."""
    for batch in store.db.execute(
        "SELECT * FROM batch_attempts WHERE run_id=? AND response_json IS NOT NULL "
        "ORDER BY started_at DESC",
        (run_id,),
    ).fetchall():
        wire = json.loads(batch["response_json"])
        items = wire.get("results", []) if isinstance(wire, dict) else []
        if not isinstance(items, list):
            continue
        items = [
            v for v in items if isinstance(v, dict) and isinstance(v.get("evaluation_key"), str)
        ]
        counts = Counter(v["evaluation_key"] for v in items)
        by_key = {v["evaluation_key"]: v for v in items}
        for index, key in enumerate(json.loads(batch["keys_json"])):
            request = store.request(key)
            if store.cached(request) or counts[key] != 1:
                continue
            try:
                value = hydrate_response(request, by_key[key], f"B{index}V")
                result = validate_result(request, parse_result(value))
            except (EvaluationError, ValidationError):
                continue
            with store.db:
                store.db.execute(
                    "INSERT OR IGNORE INTO validation_recoveries VALUES(?,?,?,?)",
                    (batch["id"], key, "application-validator-2", utcnow()),
                )
                store.save_result(request, result)


def reserve_batch(store, run_id, profile, requests):
    ident = str(uuid.uuid4())
    with store.db:
        if not store.db.execute(
            "UPDATE analysis_runs SET calls=calls+1 WHERE id=? AND calls<max_calls", (run_id,)
        ).rowcount:
            raise EvaluationError("analysis_call_budget_exhausted", fatal=True)
        generation = store.run(run_id)["retry_generation"]
        store.db.execute(
            "INSERT INTO batch_attempts VALUES(?,?,?,?,?,?,NULL,NULL,NULL,NULL)",
            (
                ident,
                run_id,
                profile,
                json.dumps([r.evaluation_key for r in requests]),
                generation,
                utcnow(),
            ),
        )
        store.db.executemany(
            "INSERT INTO batch_items VALUES(?,?,NULL)",
            [(ident, r.evaluation_key) for r in requests],
        )
    return ident


def attempts(store, run_id, key):
    return store.db.execute(
        "SELECT COUNT(*) FROM batch_items i JOIN batch_attempts b ON b.id=i.batch_id "
        "JOIN analysis_runs r ON r.id=b.run_id "
        "WHERE b.run_id=? AND i.key=? AND b.generation=r.retry_generation",
        (run_id, key),
    ).fetchone()[0]


def route_jobs(store, run_id, frozen, client):
    settings = frozen["routing"]
    with store.db:
        store.db.execute(
            "INSERT OR IGNORE INTO routing_budgets VALUES(?,?,0)",
            (run_id, settings["max_requests"]),
        )
    for row in store.db.execute(
        "SELECT route_key FROM run_jobs WHERE run_id=? ORDER BY position", (run_id,)
    ).fetchall():
        key = row["route_key"]
        if store.db.execute(
            "SELECT 1 FROM routed_selections WHERE run_id=? AND route_key=?", (run_id, key)
        ).fetchone():
            continue
        with store.db:
            if not store.db.execute(
                "UPDATE routing_budgets SET requests=requests+1 "
                "WHERE run_id=? AND requests<max_requests",
                (run_id,),
            ).rowcount:
                raise EvaluationError("routing_request_budget_exhausted", fatal=True)
        decision = client.evaluate(store.inputs(key)[0].vacancy)
        selected = select_profiles(decision["probabilities"], settings["margin"])
        with store.db:
            store.db.execute(
                "INSERT INTO routed_selections VALUES(?,?,?,?)",
                (run_id, key, json.dumps(decision), json.dumps(selected)),
            )


def aggregate(store, run_id):
    for row in store.db.execute(
        "SELECT * FROM routed_selections WHERE run_id=?", (run_id,)
    ).fetchall():
        selected = json.loads(row["selected_json"])
        requests = {r.profile_id: r for r in store.inputs(row["route_key"])}
        outcomes = {p: {"verdict": "NOT_ROUTED"} for p in ORDER if p not in selected}
        for p in selected:
            value = store.cached(requests[p])
            outcomes[p] = {
                "verdict": value.verdict if value else "ERROR",
                "evaluation_key": requests[p].evaluation_key,
            }
        verdicts = [outcomes[p]["verdict"] for p in selected]
        accepted = next((p for p in selected if outcomes[p]["verdict"] == "PASS"), None)
        if accepted:
            status = "ACCEPTED"
        elif not selected:
            status = "ROUTING_REVIEW"
        elif "ERROR" in verdicts:
            status = "PENDING"
        elif "BORDERLINE" in verdicts:
            status = "NEEDS_CLARIFICATION"
        else:
            status = "REJECTED_BY_SELECTED"
        store.save_outcome(
            run_id,
            row["route_key"],
            {
                "status": status,
                "accepted_profile": accepted,
                "selected_profiles": selected,
                "outcomes": outcomes,
                "decision": json.loads(row["decision_json"]),
            },
        )


def evaluate_queues(store, run_id, frozen, worker_factory=CodexBatchWorker):
    config = EvaluatorConfig.model_validate(frozen["model"])
    workers = {p: worker_factory(config) for p in ORDER}
    pool = ThreadPoolExecutor(max_workers=4)
    active = {}
    error = None
    try:
        while True:
            blocked_progress = False
            queues = {p: [] for p in ORDER}
            for row in store.db.execute(
                "SELECT s.* FROM routed_selections s JOIN run_jobs j "
                "ON j.run_id=s.run_id AND j.route_key=s.route_key "
                "WHERE s.run_id=? ORDER BY j.position",
                (run_id,),
            ):
                by_profile = {r.profile_id: r for r in store.inputs(row["route_key"])}
                for p in json.loads(row["selected_json"]):
                    request = by_profile[p]
                    blocked = store.db.execute(
                        "SELECT 1 FROM routed_input_errors WHERE run_id=? AND key=?",
                        (run_id, request.evaluation_key),
                    ).fetchone()
                    if (
                        not blocked
                        and store.cached(request) is None
                        and attempts(store, run_id, request.evaluation_key)
                        < config.max_attempts_per_profile
                    ):
                        queues[p].append(request)
            busy = {p for p, _, _ in active.values()}
            if error is None:
                for profile in ORDER:
                    if profile in busy or not queues[profile]:
                        continue
                    batch = []
                    for request in queues[profile][:10]:
                        if batch_size_characters([*batch, request]) > config.max_input_characters:
                            if batch:
                                break
                            with store.db:
                                store.db.execute(
                                    "INSERT OR REPLACE INTO routed_input_errors VALUES(?,?,?)",
                                    (
                                        run_id,
                                        request.evaluation_key,
                                        "input_exceeds_configured_context",
                                    ),
                                )
                            blocked_progress = True
                            continue
                        batch.append(request)
                    if not batch:
                        continue
                    try:
                        ident = reserve_batch(store, run_id, profile, batch)
                    except EvaluationError as exc:
                        error = exc.code
                        break
                    active[pool.submit(workers[profile].batch, batch)] = (profile, ident, batch)
            if not active:
                if blocked_progress and error is None:
                    continue
                break
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                _, ident, batch = active.pop(future)
                usage = response = None
                errors = {}
                try:
                    reply = future.result()
                    usage, response, errors = reply.usage, reply.wire, reply.errors
                    for request in batch:
                        saved = reply.replies.get(request.evaluation_key)
                        if saved:
                            store.save_result(request, parse_result(saved.result))
                except EvaluationError as exc:
                    errors = {r.evaluation_key: exc.code for r in batch}
                    usage = exc.usage
                    response = getattr(exc, "response", None)
                    if exc.fatal:
                        error = exc.code
                with store.db:
                    store.db.execute(
                        "UPDATE batch_attempts SET finished_at=?,usage_json=?,"
                        "response_json=?,error=? WHERE id=?",
                        (
                            utcnow(),
                            json.dumps(usage) if usage else None,
                            json.dumps(response) if response else None,
                            next(iter(errors.values()), None),
                            ident,
                        ),
                    )
                    for request in batch:
                        store.db.execute(
                            "UPDATE batch_items SET error=? WHERE batch_id=? AND key=?",
                            (errors.get(request.evaluation_key), ident, request.evaluation_key),
                        )
                aggregate(store, run_id)
    except BaseException:
        for worker in workers.values():
            worker.cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    aggregate(store, run_id)
    return error


def run_routed(store, run_id, frozen, *, worker_factory=CodexBatchWorker, client=None):
    initialize(store)
    revalidate_saved_responses(store, run_id)
    try:
        route_jobs(
            store, run_id, frozen, client or DecisionsClient(Path(frozen["routing"]["workspace"]))
        )
    except EvaluationError as exc:
        aggregate(store, run_id)
        return exc.code
    return evaluate_queues(store, run_id, frozen, worker_factory)
