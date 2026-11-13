"""Agent operations for personal context evaluation, connection setup and diagnostics."""

import fcntl
import hashlib
import json
import os
import shutil
from contextlib import closing
from pathlib import Path

from career_scout import context_engine
from career_scout.agent_service import page
from career_scout.agent_state import CommandError, envelope
from career_scout.assistant_access import connect
from career_scout.common import digest, strict_json
from career_scout.context_models import EvaluationConnection, QualityLabels
from career_scout.evaluation import EvaluationError


def put_connection(state, name, document, request_id):
    config = EvaluationConnection.model_validate(document).model_dump(mode="json")

    def write():
        state.db.execute(
            "INSERT INTO agent_connections VALUES(?,?) ON CONFLICT(name) "
            "DO UPDATE SET payload=excluded.payload",
            (name, json.dumps(config, ensure_ascii=False)),
        )
        return {"name": name, "configuration_hash": digest(config), "configuration": config}

    return state.mutate(
        request_id, "connection.put", {"name": name, "configuration": config}, write
    )


def connection(state, name):
    if not state.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='agent_connections'"
    ).fetchone():
        raise CommandError("connection_not_configured")
    row = state.db.execute("SELECT payload FROM agent_connections WHERE name=?", (name,)).fetchone()
    if not row:
        raise CommandError("connection_not_configured")
    return json.loads(row[0])


def doctor(state, name, env=None):
    config = EvaluationConnection.model_validate(connection(state, name))
    binary = config.model.executable or "codex"
    checks = [
        {"component": "codex_executable", "ok": bool(shutil.which(binary))},
        {"component": "workspace", "ok": os.access(state.root, os.W_OK)},
        {"component": "credential_reference", "ok": Path(env or state.root / ".env").is_file()},
    ]
    if config.decisions:
        native = config.decisions
        path, root = Path(native.workspace).resolve(), Path(native.workspace_root).resolve()
        checks += [
            {"component": "decisions_executable", "ok": bool(shutil.which(native.binary))},
            {
                "component": "decisions_workspace",
                "ok": path.is_dir() and path.is_relative_to(root) and os.access(path, os.W_OK),
            },
        ]
    else:
        checks.append({"component": "decisions_connection", "ok": False})
    return {
        "checks": checks,
        "ready": all(c["ok"] for c in checks),
        "external_calls_enabled": config.external_calls_enabled,
        "auth_live_verified": False,
        "network_calls": 0,
        "next": [c["component"] for c in checks if not c["ok"]],
    }


def action(state, op, parameters, request_id):
    from career_scout.analysis_store import AnalysisStore

    params = dict(parameters)
    if op == "prepare":
        params["configuration"] = connection(state, params.pop("connection"))
        if params.get("catalog"):
            path = Path(params["catalog"]).resolve()
            params["catalog"] = str(path)
            with path.open("rb") as stream:
                params["catalog_hash"] = hashlib.file_digest(stream, "sha256").hexdigest()
    else:
        dbpath = Path(params["database"]).resolve()
        if not dbpath.is_relative_to(state.root / "evaluations") or not dbpath.is_file():
            raise CommandError("evaluation_database_outside_workspace")
        context_engine.status(dbpath, params["run_id"])
        params["database"] = str(dbpath)
    with state.transaction():
        old = state.existing(request_id, "evaluation." + op, params)
        if old is not None:
            return old
        state.begin(request_id, "evaluation." + op, params)
    result = error = None
    try:
        if op == "prepare":
            if params.get("catalog"):
                data = strict_json(Path(params["catalog"]).read_text())
                catalog = data if isinstance(data, list) else data["vacancies"]
            else:
                from career_scout.campaigns import Campaigns
                from career_scout.storage import Store

                run = state.run(params["collection_run"])
                with closing(connect(Path(run["collector"]))) as db:
                    db.execute("BEGIN")
                    collector = Store.__new__(Store)
                    collector.db = db
                    catalog = Campaigns(collector).export(run["campaign_id"])["vacancies"]
            result = context_engine.prepare(
                state,
                params["context"],
                params.get("collection_run"),
                catalog,
                params["configuration"],
                params["limit"],
                params["max_calls"],
                params["max_decisions"],
            )
        else:
            with Path(str(dbpath) + ".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with closing(AnalysisStore(dbpath)) as store:
                    failure = context_engine.execute(
                        store,
                        params["run_id"],
                        additional_calls=params["additional_calls"],
                        additional_decisions=params["additional_decisions"],
                        retry_errors=params.get("retry_errors", False),
                        enable_calls=params.get("enable_calls", False),
                    )
                result = context_engine.status(dbpath, params["run_id"])
                result["execution_error"] = failure
    except (CommandError, EvaluationError) as exc:
        error = CommandError(exc.code, outcome="unknown" if "unknown" in exc.code else "failed")
    except BlockingIOError:
        error = CommandError("evaluation_busy", retryable=True)
    except Exception:
        error = CommandError("evaluation_failed", outcome="unknown")
    receipt = envelope(result, operation_id=request_id, error=error)
    with state.transaction():
        state.finish(request_id, receipt)
    return receipt


def shortlist(database, limit=20, cursor=None, include_uncertain=False):
    rows = context_engine.recommendations(database, include_uncertain=include_uncertain)
    return {
        **page(rows, limit=limit, cursor=cursor),
        "agent_context_applied": True,
        "categories": sorted({r["category"] for r in rows}),
    }


def metrics(database, run_id, labels=None):
    result = context_engine.status(database, run_id)
    result["quality"] = {"measured": False, "reason": "No labeled control set supplied"}
    if labels is None:
        return result
    values = QualityLabels.model_validate(strict_json(Path(labels).read_text())).model_dump(
        mode="json"
    )
    matrix, seen = {}, set()
    with closing(connect(database)) as db:
        frozen = json.loads(
            db.execute("SELECT config_json FROM analysis_runs WHERE id=?", (run_id,)).fetchone()[0]
        )
        if values.get("context_hash") != frozen["context"]["content_hash"]:
            raise CommandError("labels_context_mismatch")
        for item in values["cases"]:
            identity = (item["source"], item["source_id"])
            if identity in seen or item["expected"] not in {"PASS", "UNCERTAIN", "REJECT"}:
                raise CommandError("invalid_quality_labels")
            seen.add(identity)
            row = db.execute(
                "SELECT c.status,j.status FROM context_catalog c LEFT JOIN run_jobs j "
                "ON j.run_id=c.run_id AND j.route_key=c.route_key "
                "WHERE c.run_id=? AND c.source=? AND c.source_id=?",
                (run_id, *identity),
            ).fetchone()
            if row is None:
                raise CommandError("label_vacancy_not_in_run")
            actual = {
                "ACCEPTED": "PASS",
                "UNCERTAIN": "UNCERTAIN",
                "REJECTED_BY_SELECTED": "REJECT",
            }.get(
                row[1], "REJECT" if row[0] in {"RULE_EXCLUDED", "DECISIONS_EXCLUDED"} else "ERROR"
            )
            key = item["expected"] + ":" + actual
            matrix[key] = matrix.get(key, 0) + 1
    if not seen:
        raise CommandError("empty_quality_labels")
    tp = matrix.get("PASS:PASS", 0)
    positive = sum(v for k, v in matrix.items() if k.startswith("PASS:"))
    accepted = sum(v for k, v in matrix.items() if k.endswith(":PASS"))
    result["quality"] = {
        "measured": True,
        "labeled_count": len(seen),
        "confusion": matrix,
        "precision": tp / accepted if accepted else None,
        "recall": tp / positive if positive else None,
        "false_rejection_rate": matrix.get("PASS:REJECT", 0) / positive if positive else None,
        "scope": "supplied_labeled_set_only",
        "split": values["split"],
        "label_hash": digest(values),
        "accuracy": sum(v for k, v in matrix.items() if k.split(":")[0] == k.split(":")[1])
        / len(seen),
        "technical_error_rate": sum(v for k, v in matrix.items() if k.endswith(":ERROR"))
        / len(seen),
        "abstention_rate": sum(v for k, v in matrix.items() if k.endswith(":UNCERTAIN"))
        / len(seen),
    }
    return result


def history_import(state, document, request_id):
    from career_scout.analysis_store import AnalysisStore
    from career_scout.assistant_access import KINDS, record
    from career_scout.models import Identity

    if not isinstance(document, list) or len(document) > 10000:
        raise CommandError("invalid_history_import")
    for item in document:
        if not isinstance(item, dict) or set(item) != {
            "operation_id",
            "source",
            "source_id",
            "kind",
            "evidence",
            "detail",
        }:
            raise CommandError("invalid_history_event")
        Identity(source=item["source"], source_id=item["source_id"])
        if (
            item["kind"] not in KINDS
            or any(
                not isinstance(item[k], str) or not item[k].strip()
                for k in ("operation_id", "evidence")
            )
            or not isinstance(item["detail"], dict)
        ):
            raise CommandError("invalid_history_event")

    def save():
        with closing(AnalysisStore(state.root / "history.sqlite3")) as store:
            for item in document:
                record(
                    store.db, **{**item, "detail": json.dumps(item["detail"], ensure_ascii=False)}
                )
        return {
            "imported_or_existing": len(document),
            "database": str(state.root / "history.sqlite3"),
        }

    # The control transaction owns the receipt. Event operation IDs independently
    # reconcile an interruption after a history write but before the receipt.
    return state.mutate(request_id, "history.import", document, save)


def history_list(state, limit=20, cursor=None):
    from career_scout.assistant_access import events

    path = state.root / "history.sqlite3"
    if not path.is_file():
        return page([], limit=limit, cursor=cursor)
    with closing(connect(path)) as db:
        return page(events(db), limit=limit, cursor=cursor)
