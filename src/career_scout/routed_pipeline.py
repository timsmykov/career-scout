"""Four profile queues. Model threads never share a SQLite connection."""

import json
import uuid
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from pydantic import ValidationError

from career_scout import exclusions
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
CREATE TABLE IF NOT EXISTS routing_reservations(
 run_id TEXT NOT NULL,route_key TEXT NOT NULL,slots INTEGER NOT NULL,
 PRIMARY KEY(run_id,route_key));
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


def reserve_route(store, run_id, key, slots):
    """Reserve a vacancy's maximum HTTP attempts once, including across resume."""
    with store.db:
        old = store.db.execute(
            "SELECT slots FROM routing_reservations WHERE run_id=? AND route_key=?", (run_id, key)
        ).fetchone()
        delta = max(0, slots - (old[0] if old else 0))
        if (
            delta
            and not store.db.execute(
                "UPDATE routing_budgets SET requests=requests+? "
                "WHERE run_id=? AND requests+?<=max_requests",
                (delta, run_id, delta),
            ).rowcount
        ):
            raise EvaluationError("routing_request_budget_exhausted", fatal=True)
        store.db.execute(
            "INSERT INTO routing_reservations VALUES(?,?,?) "
            "ON CONFLICT(run_id,route_key) DO UPDATE SET slots=MAX(slots,excluded.slots)",
            (run_id, key, slots),
        )


def route_jobs(store, run_id, frozen, client):
    exclusions.apply_run(store, run_id)
    settings = frozen["routing"]
    with store.db:
        store.db.execute(
            "INSERT OR IGNORE INTO routing_budgets VALUES(?,?,0)",
            (run_id, settings["max_requests"]),
        )
    rows = store.db.execute(
        "SELECT route_key FROM run_jobs WHERE run_id=? AND status!='EXCLUDED' ORDER BY position",
        (run_id,),
    ).fetchall()
    if settings.get("prescreen_policy_hash"):
        route_screened(store, run_id, frozen, client, rows)
        return
    for row in rows:
        key = row["route_key"]
        if store.db.execute(
            "SELECT 1 FROM routed_selections WHERE run_id=? AND route_key=?", (run_id, key)
        ).fetchone():
            continue
        reserve_route(store, run_id, key, settings.get("native_max_attempts", 1))
        decision = client.evaluate(store.inputs(key)[0].vacancy)
        selected = select_profiles(decision["probabilities"], settings["margin"])
        with store.db:
            store.db.execute(
                "INSERT INTO routed_selections VALUES(?,?,?,?)",
                (run_id, key, json.dumps(decision), json.dumps(selected)),
            )


def screen_profile_fit(client, requests, settings):
    from career_scout.prescreen import evaluate_single

    decision = evaluate_single(client, requests[0].vacancy)
    original = (
        []
        if decision["exclusion"]
        else select_profiles(decision["probabilities"], settings["margin"])
    )
    decision["original_selected_profiles"] = original
    selected = list(original)
    if settings.get("fit_screen_policy_hash"):
        from career_scout.fit_screen import evaluate

        by_profile = {r.profile_id: r for r in requests}
        fits = {}
        for profile in original:
            fits[profile] = evaluate(client, by_profile[profile])
            if fits[profile]["excluded"]:
                selected.remove(profile)
        decision["fit_screen"] = fits
        if original and not selected:
            decision["exclusion"] = {"reason": "clear_mismatch_for_selected_profiles"}
    decision["selected_profiles"] = selected
    return decision


def route_screened(store, run_id, frozen, client, rows):
    """Four bounded native requests; only the coordinator writes SQLite."""
    from career_scout.prescreen import POLICY_HASH

    settings = frozen["routing"]
    if settings["prescreen_policy_hash"] != POLICY_HASH:
        raise EvaluationError("prescreen_policy_changed", fatal=True)
    fit_enabled = bool(settings.get("fit_screen_policy_hash"))
    if fit_enabled:
        from career_scout.fit_screen import POLICY_HASH as FIT_HASH

        if settings["fit_screen_policy_hash"] != FIT_HASH:
            raise EvaluationError("fit_screen_policy_changed", fatal=True)
    pending = [
        row
        for row in rows
        if not store.db.execute(
            "SELECT 1 FROM routed_selections WHERE run_id=? AND route_key=?",
            (run_id, row["route_key"]),
        ).fetchone()
    ]
    queue = iter((row["route_key"], store.inputs(row["route_key"])) for row in pending)
    active, error, exhausted = {}, None, False
    with ThreadPoolExecutor(max_workers=4) as pool:
        while active or not exhausted:
            while error is None and not exhausted and len(active) < 4:
                item = next(queue, None)
                if item is None:
                    exhausted = True
                    break
                key, requests = item
                reservation = (3 if fit_enabled else 1) * settings.get("native_max_attempts", 1)
                try:
                    reserve_route(store, run_id, key, reservation)
                except EvaluationError as exc:
                    error = exc
                    break
                active[pool.submit(screen_profile_fit, client, requests, settings)] = key
            if not active:
                break
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                key = active.pop(future)
                try:
                    decision = future.result()
                except EvaluationError as exc:
                    error = exc
                    continue
                selected = decision["selected_profiles"]
                with store.db:
                    store.db.execute(
                        "INSERT INTO routed_selections VALUES(?,?,?,?)",
                        (run_id, key, json.dumps(decision), json.dumps(selected)),
                    )
            if error and not active:
                break
    if error:
        raise error


def aggregate(store, run_id):
    exclusions.apply_run(store, run_id)
    for row in store.db.execute(
        "SELECT s.* FROM routed_selections s JOIN run_jobs j ON j.run_id=s.run_id "
        "AND j.route_key=s.route_key WHERE s.run_id=? AND j.status!='EXCLUDED'",
        (run_id,),
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
        decision = json.loads(row["decision_json"])
        if decision.get("exclusion"):
            status = "PRESCREENED_OUT"
        elif accepted:
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
                "decision": decision,
            },
        )


def evaluate_queues(
    store,
    run_id,
    frozen,
    worker_factory=CodexBatchWorker,
    *,
    refill=None,
    on_progress=None,
    shared_slots=False,
):
    """Four slots across runs; optional lending preserves one role per invocation.

    Refill executes on the SQLite owner thread and returns more already routed run
    IDs, or None at the source/limit boundary. Each batch retains its own run budget.
    """
    run_ids = [run_id] if isinstance(run_id, str) else list(run_id)
    exhausted = refill is None
    config = EvaluatorConfig.model_validate(frozen["model"])
    workers = {p: worker_factory(config) for p in ORDER}
    batch_limit = frozen["routing"].get("max_batch_characters") or config.max_input_characters
    batch_size = frozen["routing"].get("batch_size", 10)
    for worker in workers.values():
        if isinstance(worker, CodexBatchWorker):
            worker.batch_max_input_characters = batch_limit
    pool = ThreadPoolExecutor(max_workers=4)
    active = {}
    error = None
    try:
        while True:
            blocked_progress = False
            queues = {p: [] for p in ORDER}
            in_flight = {r.evaluation_key for _, _, batch, _, _ in active.values() for r in batch}
            for current_run in run_ids:
                for row in store.db.execute(
                    "SELECT s.* FROM routed_selections s JOIN run_jobs j "
                    "ON j.run_id=s.run_id AND j.route_key=s.route_key "
                    "WHERE s.run_id=? AND s.selected_json!='[]' AND j.status!='EXCLUDED' "
                    "ORDER BY j.position",
                    (current_run,),
                ):
                    by_profile = {r.profile_id: r for r in store.inputs(row["route_key"])}
                    if exclusions.blocked(store, next(iter(by_profile.values())).vacancy):
                        continue
                    for p in json.loads(row["selected_json"]):
                        request = by_profile[p]
                        blocked = store.db.execute(
                            "SELECT 1 FROM routed_input_errors WHERE run_id=? AND key=?",
                            (current_run, request.evaluation_key),
                        ).fetchone()
                        if (
                            not blocked
                            and request.evaluation_key not in in_flight
                            and store.cached(request) is None
                            and attempts(store, current_run, request.evaluation_key)
                            < config.max_attempts_per_profile
                        ):
                            queues[p].append((current_run, request))
            while error is None and len(active) < 4:
                busy = {p for p, _, _, _, _ in active.values()}
                available = [p for p in ORDER if queues[p] and (shared_slots or p not in busy)]
                if not available:
                    break
                # Give each waiting profile a slot before lending spare slots to
                # the largest queue. Each invocation still has one CV and role.
                unrepresented = [p for p in available if p not in busy]
                profile = (unrepresented or sorted(available, key=lambda p: -len(queues[p])))[0]
                batch, consumed = [], set()
                current_run = queues[profile][0][0]
                for candidate_run, request in queues[profile][:batch_size]:
                    if candidate_run != current_run:
                        break
                    if batch_size_characters([*batch, request]) > batch_limit:
                        if batch:
                            break
                        with store.db:
                            store.db.execute(
                                "INSERT OR REPLACE INTO routed_input_errors VALUES(?,?,?)",
                                (
                                    current_run,
                                    request.evaluation_key,
                                    "input_exceeds_configured_context",
                                ),
                            )
                        blocked_progress = True
                    else:
                        batch.append(request)
                    consumed.add(request.evaluation_key)
                queues[profile] = [
                    (rid, r) for rid, r in queues[profile] if r.evaluation_key not in consumed
                ]
                if not batch:
                    continue
                try:
                    ident = reserve_batch(store, current_run, profile, batch)
                except EvaluationError as exc:
                    error = exc.code
                    break
                used_slots = {slot for _, _, _, _, slot in active.values()}
                slot = next(p for p in ORDER if p not in used_slots) if shared_slots else profile
                active[pool.submit(workers[slot].batch, batch)] = (
                    profile,
                    ident,
                    batch,
                    current_run,
                    slot,
                )
            # A small look-ahead keeps fast profile queues fed while slow batches
            # are in flight. No extra model pool or SQLite writer is created.
            refilled = False
            if error is None and not exhausted and any(len(queues[p]) <= batch_size for p in ORDER):
                try:
                    more = refill()
                    if more is None:
                        exhausted = True
                    else:
                        for next_run in more:
                            model = json.loads(store.run(next_run)["config_json"])["model"]
                            if model != frozen["model"]:
                                raise EvaluationError("continuous_model_changed", fatal=True)
                            if next_run not in run_ids:
                                run_ids.append(next_run)
                        refilled = True
                except EvaluationError as exc:
                    error = exc.code
            if not active:
                if (blocked_progress or refilled) and error is None:
                    continue
                break
            done, _ = wait(active, timeout=0 if refilled else None, return_when=FIRST_COMPLETED)
            for future in done:
                _, ident, batch, current_run, _ = active.pop(future)
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
                aggregate(store, current_run)
                if on_progress:
                    on_progress()
    except BaseException:
        for worker in workers.values():
            worker.cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    for current_run in run_ids:
        aggregate(store, current_run)
    return error


def run_routed(store, run_id, frozen, *, worker_factory=CodexBatchWorker, client=None):
    initialize(store)
    revalidate_saved_responses(store, run_id)
    try:
        route_jobs(
            store,
            run_id,
            frozen,
            client
            or DecisionsClient(
                Path(frozen["routing"]["workspace"]),
                max_attempts=frozen["routing"].get("native_max_attempts", 1),
            ),
        )
    except EvaluationError as exc:
        aggregate(store, run_id)
        return exc.code
    return evaluate_queues(store, run_id, frozen, worker_factory)
