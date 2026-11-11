"""Explicit read-only multi-source CLI. Importing this module never calls external services."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import get_args

from pydantic import ValidationError

from career_scout.collector import collect
from career_scout.hh import Credentials, HHClient, SourceError
from career_scout.models import Identity, Source
from career_scout.search_config import RuntimeSettings, load_config
from career_scout.search_config import plan as build_plan
from career_scout.sources import create_client
from career_scout.storage import Store


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def nonnegative(value):
    number = float(value)
    if not 0 <= number < float("inf"):
        raise argparse.ArgumentTypeError("must be finite and nonnegative")
    return number


def parser():
    p = argparse.ArgumentParser(description="Read-only vacancy collector")
    p.add_argument("--config", type=Path, default=Path("configs/search.toml"))
    p.add_argument("--db", type=Path, default=Path("data/ledger.sqlite3"))
    p.add_argument("--env", type=Path, default=Path(".env"))
    p.add_argument("--token-file", type=Path, help="Writable token cache for read-only credentials")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="Upgrade the local ledger with a backup; no network")
    for name in ("plan", "collect", "probe"):
        command = sub.add_parser(name)
        command.add_argument("--query", action="append")
        command.add_argument("--source", choices=[*get_args(Source), "all"], default="hh")
        command.add_argument("--days", type=positive, default=None)
        command.add_argument("--per-page", type=positive, choices=range(1, 101), default=None)
        command.add_argument("--refresh-hours", type=nonnegative, default=None)
    for name in ("plan", "collect", "probe", "resume"):
        command = (
            sub.choices[name] if name in {"plan", "collect", "probe"} else sub.add_parser(name)
        )
        if name == "resume":
            command.add_argument("run_id")
        command.add_argument("--requests", type=positive, default=None)
        command.add_argument("--details", type=int, default=None)
        command.add_argument("--interval", type=nonnegative, default=None)
        command.add_argument("--retries", type=int, choices=range(0, 6), default=None)
    refilter = sub.add_parser("refilter")
    refilter.add_argument("run_id")
    status = sub.add_parser("status")
    status.add_argument("run_id")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("vacancy_id")
    inspect.add_argument("--source", choices=get_args(Source), default="hh")
    export = sub.add_parser("export")
    export.add_argument("run_id")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--include-excluded", action="store_true")
    return p


def runtime_options(saved, args):
    values = {**RuntimeSettings().model_dump(), **saved}
    values.update(
        {
            key: getattr(args, key)
            for key in ("requests", "details", "interval", "retries")
            if getattr(args, key) is not None
        }
    )
    return RuntimeSettings.model_validate(values).model_dump()


def client_for(plan, args, options):
    source = plan["source"]
    credentials = Credentials(args.env, token_path=args.token_file if source == "hh" else None)
    kwargs = dict(
        max_requests=options["requests"], retries=options["retries"], interval=options["interval"]
    )
    # Retain the public HH factory seam used by existing integrations/tests.
    if source == "hh":
        return HHClient(credentials, **kwargs)
    return create_client(source, credentials, options=plan.get("source_options", {}), **kwargs)


def run_source(store, run_id, plan, args, options):
    client = None
    try:
        if plan["source"] == "superjob" and not plan.get("source_options", {}).get(
            "storage_allowed"
        ):
            raise SourceError("source_storage_policy_unconfirmed")
        client = client_for(plan, args, options)
        return collect(store, client, run_id, max_details=options["details"])
    except SourceError as exc:
        with store.lock():
            return store.finish(run_id, exc)
    finally:
        if client:
            client.close()


def probe(plans, args):
    results = []
    for plan in plans:
        client = None
        result = {"source": plan["source"], "status": "blocked"}
        try:
            client = client_for(plan, args, plan["runtime"])
            page = client.search(
                plan["queries"][0],
                plan["start"],
                plan["end"],
                0,
                min(plan["per_page"], 2),
                area_ids=plan.get("source_options", {}).get("area_ids", []),
            )
            result.update(
                search_total=page.found,
                returned_items=len(page.items),
                actual_page_size=page.per_page,
                detail_checked=False,
            )
            if page.items:
                card = client.detail(page.items[0]["id"])
                result.update(
                    detail_checked=True,
                    source_id=card["source_id"],
                    description_chars=len(card["description"]),
                )
            result["status"] = "ok"
        except SourceError as exc:
            result["error"] = exc.code
        finally:
            if client:
                result["requests"] = client.used
                client.close()
        results.append(result)
    return {
        "status": "complete" if all(r["status"] == "ok" for r in results) else "partial",
        "storage": "none",
        "sources": results,
    }


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if (
        args.command in {"plan", "collect", "probe", "resume"}
        and args.details is not None
        and args.details < 0
    ):
        p.error("--details must be nonnegative")
    config = None
    plan = None
    options = None
    plans = []
    try:
        if (
            args.command in {"collect", "resume", "probe"}
            and args.token_file
            and args.token_file.resolve() == args.env.resolve()
        ):
            raise SourceError("token_cache_overlaps_credentials")
        if args.command in {"plan", "collect", "probe", "refilter"}:
            config = load_config(args.config)
        if args.command in {"plan", "collect", "probe"}:
            names = (
                [
                    s
                    for s in ("hh", "trudvsem", "habr", "superjob")
                    if s in config.sources and config.sources[s].enabled
                ]
                if args.source == "all"
                else [args.source]
            )
            if not names:
                raise SourceError("no_enabled_sources")
            for source in names:
                plan = build_plan(
                    config,
                    source=source,
                    queries=args.query or (["аналитик"] if args.command == "probe" else None),
                    days=args.days,
                    per_page=args.per_page,
                    refresh_hours=args.refresh_hours,
                )
                plan["runtime"] = runtime_options(plan["runtime"], args)
                plans.append(plan)
            if args.command == "plan":
                output = (
                    plans[0]
                    if len(plans) == 1
                    else {"source": "all", "budget_scope": "per_source", "plans": plans}
                )
                print(json.dumps(output, ensure_ascii=False, indent=2))
                return 0
            if args.command == "probe":
                output = probe(plans, args)
                print(json.dumps(output, ensure_ascii=False, indent=2))
                return 0 if output["status"] == "complete" else 2
    except SourceError as exc:
        print(json.dumps({"error": exc.code}))
        return 3
    except ValidationError:
        print(json.dumps({"error": "invalid_runtime_settings"}))
        return 3
    if args.command == "inspect":
        try:
            Identity(source=args.source, source_id=args.vacancy_id)
        except ValidationError:
            p.error("invalid source identity")
        if args.source == "hh" and not re.fullmatch(r"[0-9]+", args.vacancy_id):
            p.error("HH vacancy ID must contain digits")
    store = None
    client = None
    try:
        if (
            args.command in {"status", "inspect", "export", "resume", "refilter"}
            and not args.db.exists()
        ):
            raise SourceError("database_not_found")
        store = Store(args.db)
        if args.command == "migrate":
            output = {
                "schema_version": 4,
                "vacancy_schema_version": 2,
                "backup_path": str(store.backup_path) if store.backup_path else None,
            }
        elif args.command == "refilter":
            with store.lock():
                store.filter_run(args.run_id, config.filters)
            output = store.report(args.run_id)
        elif args.command == "status":
            output = store.report(args.run_id)
        elif args.command == "inspect":
            output = store.inspect(args.vacancy_id, source=args.source)
        elif args.command == "export":
            output = store.export(args.run_id, include_excluded=args.include_excluded)
            # An explicit output path is required; do not overwrite credentials or an existing file.
            args.output.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".export.", dir=args.output.parent)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(output, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                # Publish atomically without overwriting even if another process creates output.
                os.link(temporary, args.output)
            finally:
                os.unlink(temporary)
            output = {"output": str(args.output), "exported": len(output["vacancies"])}
        else:
            if args.command == "resume":
                saved = json.loads(store.run(args.run_id)["plan_json"])
                options = runtime_options(saved.get("runtime", {}), args)
                output = run_source(store, args.run_id, saved, args, options)
            else:
                results = []
                for plan in plans:
                    with store.lock():
                        run_id = store.create(
                            plan["queries"],
                            plan["start"],
                            plan["end"],
                            plan["per_page"],
                            plan["ttl_hours"],
                            source=plan["source"],
                            plan_snapshot=plan,
                        )
                    results.append(run_source(store, run_id, plan, args, plan["runtime"]))
                    if results[-1]["error"] == "interrupted":
                        break
                output = (
                    results[0]
                    if len(plans) == 1
                    else {
                        "status": "complete"
                        if all(r["status"] == "complete" for r in results)
                        else "partial",
                        "budget_scope": "per_source",
                        "runs": results,
                        "not_started_sources": [p["source"] for p in plans[len(results) :]],
                    }
                )
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if args.command in {"collect", "resume"}:
            return 0 if output["status"] == "complete" else 2
        return 0
    except SourceError as exc:
        print(json.dumps({"error": exc.code}))
        return 3
    except ValidationError:
        print(json.dumps({"error": "invalid_runtime_settings"}))
        return 3
    except (sqlite3.Error, OSError):
        print(json.dumps({"error": "local_storage_error"}))
        return 3
    finally:
        if client:
            client.close()
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
