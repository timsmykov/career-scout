"""Explicit read-only HH CLI. Importing this module never calls external services."""

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
    p = argparse.ArgumentParser(description="HH read-only collector")
    p.add_argument("--config", type=Path, default=Path("configs/search.toml"))
    p.add_argument("--db", type=Path, default=Path("data/ledger.sqlite3"))
    p.add_argument("--env", type=Path, default=Path(".env"))
    p.add_argument("--token-file", type=Path, help="Writable token cache for read-only credentials")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="Upgrade the local ledger with a backup; no network")
    for name in ("plan", "collect"):
        command = sub.add_parser(name)
        command.add_argument("--query", action="append")
        command.add_argument("--days", type=positive, default=None)
        command.add_argument("--per-page", type=positive, choices=range(1, 101), default=None)
        command.add_argument("--refresh-hours", type=nonnegative, default=None)
    for name in ("plan", "collect", "resume"):
        command = sub.choices[name] if name in {"plan", "collect"} else sub.add_parser(name)
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


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if (
        args.command in {"plan", "collect", "resume"}
        and args.details is not None
        and args.details < 0
    ):
        p.error("--details must be nonnegative")
    config = None
    plan = None
    options = None
    try:
        if args.command in {"plan", "collect", "refilter"}:
            config = load_config(args.config)
        if args.command in {"plan", "collect"}:
            plan = build_plan(
                config,
                queries=args.query,
                days=args.days,
                per_page=args.per_page,
                refresh_hours=args.refresh_hours,
            )
            options = dict(plan["runtime"])
            options.update(
                {
                    key: getattr(args, key)
                    for key in ("requests", "details", "interval", "retries")
                    if getattr(args, key) is not None
                }
            )
            options = RuntimeSettings.model_validate(options).model_dump()
            plan["runtime"] = options
            if args.command == "plan":
                print(json.dumps(plan, ensure_ascii=False, indent=2))
                return 0
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
                "schema_version": 3,
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
                options = {**RuntimeSettings().model_dump(), **saved.get("runtime", {})}
                options.update(
                    {
                        key: getattr(args, key)
                        for key in ("requests", "details", "interval", "retries")
                        if getattr(args, key) is not None
                    }
                )
                options = RuntimeSettings.model_validate(options).model_dump()
            credentials = Credentials(args.env, token_path=args.token_file)
            client = HHClient(
                credentials,
                max_requests=options["requests"],
                retries=options["retries"],
                interval=options["interval"],
            )
            if args.command == "collect":
                with store.lock():
                    run_id = store.create(
                        plan["queries"],
                        plan["start"],
                        plan["end"],
                        plan["per_page"],
                        plan["ttl_hours"],
                        plan_snapshot=plan,
                    )
            else:
                run_id = args.run_id
            output = collect(store, client, run_id, max_details=options["details"])
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
