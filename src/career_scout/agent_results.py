"""Read-only access to collector campaigns and the existing analysis engine."""

import fcntl
import json
from contextlib import closing
from pathlib import Path

from career_scout.agent_service import page
from career_scout.agent_state import CommandError, envelope
from career_scout.assistant_access import analysis_store, connect
from career_scout.common import digest


def collection_results(state, run_id, *, limit=20, cursor=None, full=False):
    from career_scout.campaigns import Campaigns
    from career_scout.storage import Store

    run = state.run(run_id)
    if not Path(run["collector"]).exists():
        return page([], limit=limit, cursor=cursor)
    with closing(connect(Path(run["collector"]))) as db:
        if not db.execute("SELECT 1 FROM campaigns WHERE id=?", (run["campaign_id"],)).fetchone():
            return page([], limit=limit, cursor=cursor)
        store = Store.__new__(Store)
        store.db = db
        rows = Campaigns(store).export(run["campaign_id"])["vacancies"]
    if not full:
        keys = ("source", "source_id", "title", "company", "url", "published_at", "attributes")
        rows = [{key: r.get(key) for key in keys} for r in rows]
    return page(rows, limit=limit, cursor=cursor)


def shortlist(database, *, limit=20, cursor=None, profile=None):
    from career_scout.shortlist import recommendations

    with closing(connect(database)) as db:
        rows = recommendations(analysis_store(db), profile=profile, limit=10000)
    return {
        **page(rows, limit=limit, cursor=cursor),
        "truncated": len(rows) == 10000,
        "mode": "existing_analysis",
        "agent_context_applied": False,
    }


def explain(database, source, source_id):
    from career_scout import exclusions
    from career_scout.assistant_access import events

    with closing(connect(database)) as db:
        analysis_store(db)
        history = events(db, source, source_id)
        blocked = exclusions.identity_blocked(db, source, source_id)
        exclusion = None
        if blocked:
            row = db.execute(
                "SELECT stage,reason,excluded_at FROM vacancy_exclusions "
                "WHERE source=? AND source_id=?",
                (source, source_id),
            ).fetchone()
            exclusion = dict(row)
        row = db.execute(
            "SELECT j.run_id,j.route_key,j.status,j.outcome_json FROM run_jobs j "
            "JOIN analysis_runs r ON r.id=j.run_id JOIN jobs m ON m.route_key=j.route_key "
            "JOIN inputs i ON i.key=json_extract(m.manifest_json,'$.keys[0]') "
            "WHERE json_extract(i.payload,'$.vacancy.source')=? AND "
            "json_extract(i.payload,'$.vacancy.source_id')=? "
            "ORDER BY r.created_at DESC,r.rowid DESC LIMIT 1",
            (source, source_id),
        ).fetchone()
        if not row and not history and not exclusion:
            raise CommandError("unknown_vacancy")
        evaluations = []
        if row:
            for request in analysis_store(db).inputs(row["route_key"]):
                result = analysis_store(db).cached(request)
                if result:
                    evaluations.append(
                        {
                            "resume_id": request.resume.resume_id,
                            "evaluation_key": request.evaluation_key,
                            "verdict": result.verdict,
                            "reason": result.reason,
                            "assessments": [a.model_dump(mode="json") for a in result.assessments],
                        }
                    )
        return {
            "source": source,
            "source_id": source_id,
            "excluded": blocked,
            "exclusion": exclusion,
            "evaluations": evaluations,
            "latest_analysis": {
                "run_id": row["run_id"],
                "status": row["status"],
                "outcome": json.loads(row["outcome_json"]) if row["outcome_json"] else None,
            }
            if row
            else None,
            "history": [{k: r[k] for k in ("kind", "created_at", "operation_id")} for r in history],
        }


def analysis_status(database, run_id):
    from career_scout.analysis import report

    with closing(connect(database)) as db:
        store = analysis_store(db)
        store.path = Path(database).resolve()
        return {"mode": "legacy_analysis", "agent_context_applied": False, **report(store, run_id)}


def analysis_action(state, action, parameters, request_id):
    """Explicit bridge to existing analysis; never compile free-text preferences silently."""
    from career_scout.analysis import prepare, run_pending
    from career_scout.analysis_store import AnalysisStore
    from career_scout.evaluation import EvaluationError

    payload = dict(parameters)
    if action == "prepare":
        config = Path(payload["config"]).resolve()
        payload["config"] = str(config)
        payload["config_hash"] = digest(config.read_text())
    else:
        payload["database"] = str(Path(payload["database"]).resolve())
    with state.transaction():
        old = state.existing(request_id, "analysis." + action, payload)
        if old is not None:
            return old
        state.begin(request_id, "analysis." + action, payload)
    error, result = None, None
    try:
        if action == "prepare":
            result = prepare(config, limit=payload["limit"], max_calls=payload["max_calls"])
        else:
            database = Path(payload["database"])
            if not database.is_file():
                raise CommandError("analysis_database_missing")
            with Path(str(database) + ".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with closing(AnalysisStore(database)) as store:
                    result = run_pending(
                        store,
                        payload["run_id"],
                        additional_calls=payload["additional_calls"],
                        retry_errors=payload["retry_errors"],
                    )
    except EvaluationError as exc:
        error = CommandError(exc.code, outcome="unknown" if "unknown" in exc.code else "failed")
    except BlockingIOError:
        error = CommandError("analysis_busy", retryable=True)
    except CommandError as exc:
        error = exc
    except Exception:
        error = CommandError("analysis_execution_failed", outcome="unknown")
    receipt = envelope(
        {"mode": "legacy_analysis", "agent_context_applied": False, "analysis": result},
        operation_id=request_id,
        error=error,
    )
    with state.transaction():
        state.finish(request_id, receipt)
    return receipt
