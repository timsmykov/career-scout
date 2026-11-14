"""Agent-facing, JSON-first CLI backed by shared local service operations."""

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from pydantic import ValidationError

from career_scout import agent_results, agent_service, context_commands
from career_scout.agent_models import ContextInput, RunLimits, SearchPlanInput
from career_scout.agent_state import AgentState, CommandError, envelope
from career_scout.common import SourceError, strict_json
from career_scout.context_models import EvaluationConnection, QualityLabels
from career_scout.evaluation import EvaluationError
from career_scout.models import Source

CAPABILITIES = {
    "context_versions": True,
    "arbitrary_resume_count": True,
    "free_text_preferences": True,
    "agent_search_plans": True,
    "bounded_collection": True,
    "legacy_analysis": True,
    "generic_context_evaluation": True,
    "explicit_uncertainty": True,
    "portable_connections": True,
    "automatic_applications": False,
    "model_billing": "codex_subscription_for_legacy_evaluators",
}
OPERATIONS = (
    "history.import",
    "history.list",
    "connection.put",
    "connection.show",
    "doctor",
    "evaluation.prepare",
    "evaluation.run",
    "evaluation.resume",
    "evaluation.status",
    "evaluation.shortlist",
    "evaluation.metrics",
    "context.create",
    "context.show",
    "context.update",
    "context.list",
    "plan.put",
    "plan.show",
    "plan.preview",
    "plan.list",
    "run.start",
    "run.execute",
    "run.resume",
    "run.status",
    "run.list",
    "run.results",
    "operation.show",
    "analysis.prepare",
    "analysis.run",
    "analysis.resume",
    "analysis.status",
    "shortlist.list",
    "explain",
    "schema",
    "version",
)
MUTATIONS = {
    "history.import",
    "connection.put",
    "evaluation.prepare",
    "evaluation.run",
    "evaluation.resume",
    "context.create",
    "context.update",
    "plan.put",
    "run.start",
    "run.execute",
    "run.resume",
    "analysis.prepare",
    "analysis.run",
    "analysis.resume",
}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's message may echo arbitrary input; expose only the command contract.
        raise CommandError("invalid_arguments", details={"usage": self.format_usage().strip()})


def positive(value):
    value = int(value)
    if value < 1:
        raise ValueError("positive integer required")
    return value


def nonnegative(value):
    value = int(value)
    if value < 0:
        raise ValueError("nonnegative integer required")
    return value


def parser():
    common = Parser(add_help=False)
    common.add_argument("--workspace", type=Path, default=argparse.SUPPRESS)
    common.add_argument(
        "--env",
        type=Path,
        default=argparse.SUPPRESS,
        help="Credential file reference; contents are never copied",
    )
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="JSON is the default output for operations",
    )
    root = Parser(prog="career-scout", parents=[common], description=__doc__)
    groups = root.add_subparsers(dest="group", required=True)

    def leaf(parent, name, operation):
        p = parent.add_parser(name, parents=[common])
        p.set_defaults(operation=operation)
        if operation in MUTATIONS:
            p.add_argument("--request-id", required=True)
        return p

    def paging(p):
        p.add_argument("--limit", type=positive, default=20)
        p.add_argument("--cursor")

    context = groups.add_parser("context", parents=[common]).add_subparsers(required=True)
    p = leaf(context, "create", "context.create")
    p.add_argument("--input", required=True)
    p = leaf(context, "show", "context.show")
    p.add_argument("context_id")
    p.add_argument("--version", type=positive)
    p.add_argument("--summary", action="store_true")
    p = leaf(context, "update", "context.update")
    p.add_argument("context_id")
    p.add_argument("--input", required=True)
    p.add_argument("--if-version", type=positive, required=True)
    paging(leaf(context, "list", "context.list"))
    plans = groups.add_parser("plan", parents=[common]).add_subparsers(required=True)
    p = leaf(plans, "put", "plan.put")
    p.add_argument("--context", required=True)
    p.add_argument("--input", required=True)
    for name in ("show", "preview"):
        p = leaf(plans, name, "plan." + name)
        p.add_argument("plan_id")
    p = leaf(plans, "list", "plan.list")
    p.add_argument("--context", required=True)
    paging(p)
    runs = groups.add_parser("run", parents=[common]).add_subparsers(required=True)
    p = leaf(runs, "start", "run.start")
    p.add_argument("--context", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--limits")
    p.add_argument("--mode", choices=("initial", "new"), default="initial")
    for name in ("execute", "resume"):
        p = leaf(runs, name, "run." + name)
        p.add_argument("run_id")
        p.add_argument("--additional-requests", type=nonnegative, default=0)
    p = leaf(runs, "status", "run.status")
    p.add_argument("run_id")
    p = leaf(runs, "results", "run.results")
    p.add_argument("run_id")
    p.add_argument("--full", action="store_true")
    paging(p)
    p = leaf(runs, "list", "run.list")
    p.add_argument("--context")
    paging(p)
    operations = groups.add_parser("operation", parents=[common]).add_subparsers(required=True)
    p = leaf(operations, "show", "operation.show")
    p.add_argument("request_id")
    analysis = groups.add_parser("analysis", parents=[common]).add_subparsers(required=True)
    p = leaf(analysis, "prepare", "analysis.prepare")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--limit", type=positive, default=20)
    p.add_argument("--max-calls", type=nonnegative, required=True)
    for name in ("run", "resume", "status"):
        p = leaf(analysis, name, "analysis." + name)
        p.add_argument("run_id")
        p.add_argument("--db", type=Path, required=True)
        if name != "status":
            p.add_argument("--additional-calls", type=nonnegative, default=0)
            p.add_argument("--retry-errors", action="store_true")
    shortlist = groups.add_parser("shortlist", parents=[common]).add_subparsers(required=True)
    p = leaf(shortlist, "list", "shortlist.list")
    p.add_argument("--analysis-db", type=Path, required=True)
    p.add_argument("--profile")
    paging(p)
    p = leaf(groups, "explain", "explain")
    p.add_argument("--analysis-db", type=Path, required=True)
    p.add_argument("--source", choices=Source.__args__, required=True)
    p.add_argument("--id", required=True)
    connections = groups.add_parser("connection", parents=[common]).add_subparsers(required=True)
    history = groups.add_parser("history", parents=[common]).add_subparsers(required=True)
    p = leaf(history, "import", "history.import")
    p.add_argument("--input", required=True)
    paging(leaf(history, "list", "history.list"))
    p = leaf(connections, "put", "connection.put")
    p.add_argument("name")
    p.add_argument("--input", required=True)
    p = leaf(connections, "show", "connection.show")
    p.add_argument("name")
    p = leaf(groups, "doctor", "doctor")
    p.add_argument("--connection", default="default")
    evaluation = groups.add_parser("evaluation", parents=[common]).add_subparsers(required=True)
    p = leaf(evaluation, "prepare", "evaluation.prepare")
    p.add_argument("--context", required=True)
    p.add_argument("--connection", default="default")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--catalog", type=Path)
    source.add_argument("--collection-run")
    p.add_argument("--limit", type=positive, default=100)
    p.add_argument("--max-calls", type=nonnegative, required=True)
    p.add_argument("--max-decisions", type=nonnegative, required=True)
    for name in ("run", "resume", "status", "metrics", "shortlist"):
        p = leaf(evaluation, name, "evaluation." + name)
        p.add_argument("--db", type=Path, required=True)
        if name != "shortlist":
            p.add_argument("run_id")
        if name in {"run", "resume"}:
            p.add_argument("--additional-calls", type=nonnegative, default=0)
            p.add_argument("--additional-decisions", type=nonnegative, default=0)
            p.add_argument("--retry-errors", action="store_true")
            p.add_argument("--enable-calls", action="store_true")
        elif name == "shortlist":
            paging(p)
            p.add_argument("--include-uncertain", action="store_true")
        elif name == "metrics":
            p.add_argument("--labels", type=Path)
    p = leaf(groups, "schema", "schema")
    p.add_argument("--name", choices=("context", "plan", "limits", "connection", "labels"))
    leaf(groups, "version", "version")
    return root


def read_input(path):
    try:
        if path == "-":
            text = sys.stdin.read(16_000_001)
        else:
            p = Path(path)
            if p.stat().st_size > 16_000_000:
                raise CommandError("input_too_large")
            text = p.read_text()
        if len(text) > 16_000_000:
            raise CommandError("input_too_large")
        return strict_json(text)
    except (OSError, ValueError):
        raise CommandError("input_unreadable_or_invalid_json") from None


def summary(value):
    return agent_service.context_metadata(value)


def dispatch(args):
    op = args.operation
    if op == "schema":
        schemas = {
            "context": ContextInput,
            "plan": SearchPlanInput,
            "limits": RunLimits,
            "connection": EvaluationConnection,
            "labels": QualityLabels,
        }
        return envelope(
            {
                "operations": OPERATIONS,
                "capabilities": CAPABILITIES,
                "schemas": {
                    key: model.model_json_schema()
                    for key, model in schemas.items()
                    if not args.name or args.name == key
                },
            }
        )
    if op == "version":
        from importlib.metadata import version

        return envelope(
            {
                "version": version("career-scout"),
                "protocol_version": 1,
                "capabilities": CAPABILITIES,
            }
        )
    if op == "shortlist.list":
        return envelope(
            agent_results.shortlist(
                args.analysis_db, limit=args.limit, cursor=args.cursor, profile=args.profile
            )
        )
    if op == "explain":
        return envelope(agent_results.explain(args.analysis_db, args.source, args.id))
    if op == "analysis.status":
        return envelope(agent_results.analysis_status(args.db, args.run_id))
    if op == "evaluation.status":
        return envelope(context_commands.context_engine.status(args.db, args.run_id))
    if op == "evaluation.shortlist":
        return envelope(
            context_commands.shortlist(args.db, args.limit, args.cursor, args.include_uncertain)
        )
    if op == "evaluation.metrics":
        return envelope(context_commands.metrics(args.db, args.run_id, args.labels))
    default = (
        Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
        / "career-scout"
    )
    with closing(AgentState(getattr(args, "workspace", default), write=op in MUTATIONS)) as state:
        if op == "history.import":
            return context_commands.history_import(state, read_input(args.input), args.request_id)
        if op == "history.list":
            return envelope(context_commands.history_list(state, args.limit, args.cursor))
        if op == "connection.put":
            return context_commands.put_connection(
                state, args.name, read_input(args.input), args.request_id
            )
        if op == "connection.show":
            return envelope(context_commands.connection(state, args.name))
        if op == "doctor":
            return envelope(
                context_commands.doctor(state, args.connection, getattr(args, "env", None))
            )
        if op == "evaluation.prepare":
            return context_commands.action(
                state,
                "prepare",
                {
                    "context": args.context,
                    "connection": args.connection,
                    "catalog": str(args.catalog) if args.catalog else None,
                    "collection_run": args.collection_run,
                    "limit": args.limit,
                    "max_calls": args.max_calls,
                    "max_decisions": args.max_decisions,
                },
                args.request_id,
            )
        if op in {"evaluation.run", "evaluation.resume"}:
            return context_commands.action(
                state,
                "run",
                {
                    "database": str(args.db),
                    "run_id": args.run_id,
                    "additional_calls": args.additional_calls,
                    "additional_decisions": args.additional_decisions,
                    "retry_errors": args.retry_errors,
                    "enable_calls": args.enable_calls,
                },
                args.request_id,
            )
        if op == "context.create":
            return agent_service.context_create(state, read_input(args.input), args.request_id)
        if op == "context.update":
            return agent_service.context_update(
                state, args.context_id, read_input(args.input), args.if_version, args.request_id
            )
        if op == "context.show":
            value = state.context(args.context_id, args.version)
            return envelope(summary(value) if args.summary else value)
        if op == "context.list":
            rows = [
                summary(state.context(r[0]))
                for r in state.db.execute("SELECT id FROM contexts ORDER BY created_at,id")
            ]
            return envelope(agent_service.page(rows, limit=args.limit, cursor=args.cursor))
        if op == "plan.put":
            return agent_service.plan_put(
                state, args.context, read_input(args.input), args.request_id
            )
        if op in ("plan.show", "plan.preview"):
            value = state.plan(args.plan_id)
            if op == "plan.preview":
                value["effective_plans"] = SearchPlanInput.model_validate(
                    value["document"]
                ).compile()
                value["network_calls"] = 0
                value["free_text_preferences_applied"] = False
            return envelope(value)
        if op == "plan.list":
            state.context(args.context)
            rows = [
                state.plan(r[0])
                for r in state.db.execute(
                    "SELECT id FROM plans WHERE context_id=? ORDER BY created_at,id",
                    (args.context,),
                )
            ]
            return envelope(agent_service.page(rows, limit=args.limit, cursor=args.cursor))
        if op == "run.start":
            return agent_service.run_start(
                state,
                args.context,
                args.plan,
                read_input(args.limits) if args.limits else {},
                args.mode,
                getattr(args, "env", state.root / ".env"),
                args.request_id,
            )
        if op in ("run.execute", "run.resume"):
            return agent_service.run_execute(
                state,
                args.run_id,
                args.request_id,
                additional_requests=args.additional_requests,
                env_path=getattr(args, "env", None),
            )
        if op == "run.status":
            return envelope(agent_service.run_status(state, args.run_id))
        if op == "run.results":
            return envelope(
                agent_results.collection_results(
                    state, args.run_id, limit=args.limit, cursor=args.cursor, full=args.full
                )
            )
        if op == "run.list":
            if args.context:
                state.context(args.context)
                ids = state.db.execute(
                    "SELECT id FROM runs WHERE context_id=? ORDER BY created_at,id", (args.context,)
                )
            else:
                ids = state.db.execute("SELECT id FROM runs ORDER BY created_at,id")
            return envelope(
                agent_service.page(
                    [agent_service.run_status(state, r[0]) for r in ids],
                    limit=args.limit,
                    cursor=args.cursor,
                )
            )
        if op == "operation.show":
            row = state.db.execute(
                "SELECT status,receipt,operation FROM operations WHERE request_id=?",
                (args.request_id,),
            ).fetchone()
            if row is None:
                raise CommandError("unknown_operation")
            return envelope(
                {
                    "request_id": args.request_id,
                    "status": row["status"],
                    "operation": row["operation"],
                    "receipt": json.loads(row["receipt"]) if row["receipt"] else None,
                }
            )
        if op.startswith("analysis."):
            action = op.split(".")[1]
            params = (
                {"config": str(args.config), "limit": args.limit, "max_calls": args.max_calls}
                if action == "prepare"
                else {
                    "database": str(args.db),
                    "run_id": args.run_id,
                    "additional_calls": args.additional_calls,
                    "retry_errors": args.retry_errors,
                }
            )
            return agent_results.analysis_action(state, action, params, args.request_id)
    raise CommandError("unknown_operation")


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        response = dispatch(args)
    except CommandError as exc:
        response = envelope(error=exc)
    except ValidationError as exc:
        response = envelope(
            error=CommandError(
                "invalid_input",
                details=[
                    {"location": list(e["loc"]), "type": e["type"]}
                    for e in exc.errors(include_input=False)
                ],
            )
        )
    except (SourceError, EvaluationError) as exc:
        response = envelope(error=CommandError(exc.code))
    except (OSError, sqlite3.Error, ValueError):
        response = envelope(error=CommandError("storage_or_input_error"))
    except KeyboardInterrupt:
        response = envelope(error=CommandError("operation_interrupted", outcome="unknown"))
    except Exception:
        response = envelope(error=CommandError("internal_error", outcome="unknown"))
    print(json.dumps(response, ensure_ascii=False))
    return 0 if response["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
