"""Shared operations for the CLI and future adapters; no professional taxonomy."""

import fcntl
import json
import uuid
from contextlib import closing
from pathlib import Path

from career_scout.agent_models import ContextInput, RunLimits, SearchPlanInput
from career_scout.agent_state import AgentState, CommandError, envelope, identifier
from career_scout.common import SourceError, digest, utcnow


def context_metadata(value):
    from career_scout.evaluation import text_hash

    result = {k: v for k, v in value.items() if k != "document"}
    result["resumes"] = [
        {"id": r["id"], "title": r["title"], "text_hash": text_hash(r["text"])}
        for r in value["document"]["resumes"]
    ]
    result["preference_count"] = len(value["document"]["preferences"])
    return result


def context_create(state: AgentState, document, request_id):
    value = ContextInput.model_validate(document).model_dump(mode="json")

    def create():
        cid = identifier("ctx")
        state.db.execute("INSERT INTO contexts VALUES(?,1,?)", (cid, utcnow()))
        state.db.execute(
            "INSERT INTO context_versions VALUES(?,1,?,?,?)",
            (cid, json.dumps(value, ensure_ascii=False), digest(value), utcnow()),
        )
        return context_metadata(state.context(cid))

    identity = {k: v for k, v in value.items() if k != "fact_rules" or v}
    return state.mutate(request_id, "context.create", identity, create)


def context_update(state, context_id, patch, expected_version, request_id):
    if not isinstance(patch, dict) or set(patch) - set(ContextInput.model_fields):
        raise CommandError("invalid_context_patch")

    def update():
        current = state.context(context_id)
        if current["version"] != expected_version:
            raise CommandError("version_conflict", retryable=True)
        value = ContextInput.model_validate({**current["document"], **patch}).model_dump(
            mode="json"
        )
        normalized_current = ContextInput.model_validate(current["document"]).model_dump(
            mode="json"
        )
        if value == normalized_current:
            return {**context_metadata(current), "unchanged": True}
        version = expected_version + 1
        state.db.execute("UPDATE contexts SET version=? WHERE id=?", (version, context_id))
        state.db.execute(
            "INSERT INTO context_versions VALUES(?,?,?,?,?)",
            (context_id, version, json.dumps(value, ensure_ascii=False), digest(value), utcnow()),
        )
        return context_metadata(state.context(context_id))

    return state.mutate(
        request_id,
        "context.update",
        {"context_id": context_id, "patch": patch, "if_version": expected_version},
        update,
    )


def plan_put(state, context_id, document, request_id):
    plan = SearchPlanInput.model_validate(document)
    # Freeze only on run creation; plan preview itself makes no network calls.
    plan.compile()
    value = plan.model_dump(mode="json")

    def create():
        context = state.context(context_id)
        pid = identifier("plan")
        state.db.execute(
            "INSERT INTO plans VALUES(?,?,?,?,?)",
            (pid, context_id, context["version"], json.dumps(value, ensure_ascii=False), utcnow()),
        )
        return state.plan(pid)

    return state.mutate(request_id, "plan.put", {"context_id": context_id, "plan": value}, create)


def run_start(state, context_id, plan_id, limits, mode, env_path, request_id):
    limits = RunLimits.model_validate(limits).model_dump(mode="json")
    if mode not in ("initial", "new"):
        raise CommandError("invalid_run_mode")
    env_path = str(Path(env_path).resolve())

    def start():
        context = state.context(context_id)
        saved = state.plan(plan_id)
        if saved["context_id"] != context_id:
            raise CommandError("plan_context_mismatch")
        if saved["context_version"] != context["version"]:
            raise CommandError("plan_context_stale", retryable=True)
        plan = SearchPlanInput.model_validate(saved["document"])
        sj = plan.source_options.get("superjob")
        if "superjob" in plan.sources and (sj is None or not sj.storage_allowed):
            raise CommandError("source_storage_policy_unconfirmed")
        rid, cid = identifier("run"), str(uuid.uuid4())
        now = utcnow()
        state.db.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?,?,'queued',?,?,?,?,?,NULL,NULL,?,?)",
            (
                rid,
                context_id,
                context["version"],
                plan_id,
                cid,
                mode,
                json.dumps(plan.compile(mode), ensure_ascii=False),
                json.dumps(limits),
                str(state.root / "collector.sqlite3"),
                env_path,
                str(state.root / "auth" / "hh-token.json"),
                now,
                now,
            ),
        )
        return run_status(state, rid)

    return state.mutate(
        request_id,
        "run.start",
        {"context": context_id, "plan": plan_id, "limits": limits, "mode": mode, "env": env_path},
        start,
    )


def run_status(state, run_id):
    run = state.run(run_id)
    report_origin = "checkpoint"
    if Path(run["collector"]).is_file():
        from career_scout.assistant_access import connect
        from career_scout.campaigns import Campaigns
        from career_scout.storage import Store

        with closing(connect(Path(run["collector"]))) as db:
            db.execute("BEGIN")
            initialized = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='campaigns'"
            ).fetchone()
            if (
                initialized
                and db.execute(
                    "SELECT 1 FROM campaigns WHERE id=?", (run["campaign_id"],)
                ).fetchone()
            ):
                store = Store.__new__(Store)
                store.db = db
                run["report"] = Campaigns(store).report(run["campaign_id"])
                report_origin = "collector_snapshot"
    if run["status"] == "running":
        path = state.root / "run-locks" / (digest(run_id) + ".lock")
        if not path.exists():
            run["status"] = "interrupted"
        else:
            with path.open("r") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    run["status"] = "interrupted"
    used = (run["report"] or {}).get("totals", {}).get("requests", 0)
    return {
        "run_id": run_id,
        "context_id": run["context_id"],
        "context_version": run["context_version"],
        "plan_id": run["plan_id"],
        "campaign_id": run["campaign_id"],
        "run_status": run["status"],
        "kind": "collection",
        "limits": run["limits_json"],
        "report": run["report"],
        "report_origin": report_origin,
        "error": run["error"],
        "created_at": run["created_at"],
        "updated_at": run["updated_at"],
        "semantic_preferences_applied": False,
        "remaining_requests": max(0, run["limits_json"]["max_requests"] - used),
        "context_is_newer": state.context(run["context_id"])["version"] != run["context_version"],
    }


def source_factory(run, frozen, budget):
    from career_scout.hh import Credentials
    from career_scout.sources import create_client

    if frozen["source"] in {"hh", "superjob"} and not Path(run["env_path"]).is_file():
        raise SourceError("connection_env_missing")
    credentials = Credentials(Path(run["env_path"]), token_path=Path(run["token_path"]))
    return create_client(
        frozen["source"],
        credentials,
        options=frozen["source_options"],
        max_requests=budget,
        retries=frozen["runtime"]["retries"],
        interval=frozen["runtime"]["interval"],
    )


def run_execute(
    state, run_id, request_id, *, additional_requests=0, client_factory=None, env_path=None
):
    from career_scout.campaigns import Campaigns
    from career_scout.storage import Store

    if type(additional_requests) is not int or additional_requests < 0:
        raise CommandError("invalid_additional_budget")
    payload = {"run_id": run_id, "additional_requests": additional_requests}
    if env_path is not None:
        payload["env"] = str(Path(env_path).resolve())
    # Durable intent is checked first, before any source/database action.
    with state.transaction():
        old = state.existing(request_id, "run.execute", payload)
        if old is not None:
            return old
    lock_dir = state.root / "run-locks"
    lock_dir.mkdir(mode=0o700, exist_ok=True)
    # Validate identity before using it as part of a filesystem path.
    run = state.run(run_id)
    with (lock_dir / (digest(run_id) + ".lock")).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CommandError("run_busy", retryable=True) from None
        with state.transaction():
            old = state.existing(request_id, "run.execute", payload)
            if old is not None:
                return old
            run = state.run(run_id)
            if run["status"] == "complete":
                state.begin(request_id, "run.execute", payload)
                receipt = envelope(run_status(state, run_id), operation_id=request_id)
                state.finish(request_id, receipt)
                return receipt
            state.begin(request_id, "run.execute", payload)
            run["limits_json"]["max_requests"] += additional_requests
            RunLimits.model_validate(run["limits_json"])
            state.db.execute(
                "UPDATE runs SET status='running',limits_json=?,env_path=?,error=NULL,updated_at=? "
                "WHERE id=?",
                (
                    json.dumps(run["limits_json"]),
                    payload.get("env", run["env_path"]),
                    utcnow(),
                    run_id,
                ),
            )
            run["env_path"] = payload.get("env", run["env_path"])
        store = None
        error, report = None, None
        try:
            store = Store(Path(run["collector"]))
            campaigns = Campaigns(store)
            cid = campaigns.create_frozen(
                run["frozen"], run["mode"], campaign_id=run["campaign_id"]
            )

            def factory(frozen, budget):
                used = campaigns.report(cid)["totals"]["requests"]
                remaining = run["limits_json"]["max_requests"] - used
                if remaining <= 0:
                    raise SourceError("total_request_budget_exhausted")
                return (client_factory or source_factory)(run, frozen, min(budget, remaining))

            report = campaigns.drive(
                cid,
                factory,
                chunk_requests=run["limits_json"]["chunk_requests"],
                max_rounds=run["limits_json"]["max_rounds"],
                sleep=lambda _: None,
            )
        except SourceError as exc:
            error = exc.code
        except BlockingIOError:
            error = "collector_busy"
        except Exception:
            error = "execution_failed"
        finally:
            if store is not None:
                store.close()
        status = "complete" if report and report["status"] == "complete" else "partial"
        with state.transaction():
            state.db.execute(
                "UPDATE runs SET status=?,report=?,error=?,updated_at=? WHERE id=?",
                (status, json.dumps(report) if report else None, error, utcnow(), run_id),
            )
            receipt = envelope(run_status(state, run_id), operation_id=request_id)
            state.finish(request_id, receipt)
        return receipt


def page(items, *, limit=20, cursor=None):
    import base64

    if type(limit) is not int or not 1 <= limit <= 200:
        raise CommandError("invalid_limit")
    signature = digest(items)
    offset = 0
    if cursor:
        try:
            saved = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            if saved["signature"] != signature:
                raise CommandError("cursor_stale", retryable=True)
            offset = saved["offset"]
            if type(offset) is not int or offset < 0:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise CommandError("invalid_cursor") from None
    next_offset = offset + limit
    next_cursor = None
    if next_offset < len(items):
        next_cursor = base64.urlsafe_b64encode(
            json.dumps({"offset": next_offset, "signature": signature}).encode()
        ).decode()
    return {"items": items[offset:next_offset], "total": len(items), "next_cursor": next_cursor}
