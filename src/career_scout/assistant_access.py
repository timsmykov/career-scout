"""On-demand career assistant I/O. No collection, inference, or credential access."""

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from career_scout import exclusions
from career_scout.analysis_store import APPLICATION_ID, AnalysisStore
from career_scout.evaluation import ResumeSnapshot, text_hash
from career_scout.shortlist import recommendations

KINDS = (
    "shown",
    "dismissed",
    "saved",
    "applied",
    "employer_reply",
    "interview",
    "test_assignment",
    "offer",
    "rejected",
    "withdrawn",
    "follow_up",
    "resume_updated",
    "legacy_observation",
    "reopened",
)
SOURCES = ("hh", "habr", "superjob", "trudvsem")


def connect(path, *, write=False):
    db = sqlite3.connect(
        Path(path).resolve().as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=10
    )
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def analysis_store(db):
    if db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
        raise ValueError("not_an_analysis_database")
    store = AnalysisStore.__new__(AnalysisStore)
    store.db = db
    return store


def events(db, source=None, source_id=None):
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='career_assistant_events'"
    ).fetchone():
        return []
    where, args = [], []
    for field, value in (("source", source), ("source_id", source_id)):
        if value is not None:
            where.append(field + "=?")
            args.append(value)
    sql = "SELECT * FROM career_assistant_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return [dict(r) for r in db.execute(sql + " ORDER BY sequence", args)]


def record(db, operation_id, source, source_id, kind, evidence, detail):
    exclusions.initialize(analysis_store(db))
    if not all(
        isinstance(x, str) and x.strip() for x in (operation_id, source, source_id, kind, evidence)
    ):
        raise ValueError("identity_and_evidence_required")
    if kind not in KINDS or source not in (*SOURCES, "legacy"):
        raise ValueError("invalid_event")
    payload = json.dumps([source, source_id, kind, evidence, detail], ensure_ascii=False)
    fingerprint = hashlib.sha256(payload.encode()).hexdigest()
    with db:
        db.execute("""CREATE TABLE IF NOT EXISTS career_assistant_events (
          sequence INTEGER PRIMARY KEY,operation_id TEXT NOT NULL UNIQUE,
          source TEXT NOT NULL,source_id TEXT NOT NULL,kind TEXT NOT NULL,
          evidence TEXT NOT NULL,detail TEXT NOT NULL,created_at TEXT NOT NULL,
          fingerprint TEXT NOT NULL)""")
        old = db.execute(
            "SELECT * FROM career_assistant_events WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if old:
            if old["fingerprint"] != fingerprint:
                raise ValueError("operation_id_conflict")
            if kind == "reopened":
                exclusions.restore_owner(
                    analysis_store(db),
                    source,
                    source_id,
                    operation_id=operation_id,
                    evidence=evidence,
                )
            return dict(old)
        db.execute(
            """INSERT INTO career_assistant_events
          (operation_id,source,source_id,kind,evidence,detail,created_at,fingerprint)
          VALUES(?,?,?,?,?,?,?,?)""",
            (
                operation_id,
                source,
                source_id,
                kind,
                evidence,
                detail,
                datetime.now(UTC).isoformat(),
                fingerprint,
            ),
        )
        if kind in {"dismissed", "withdrawn", "rejected"}:
            store = analysis_store(db)
            exclusions.initialize(store)
            exclusions.record_identity(
                store,
                source,
                source_id,
                stage="owner",
                reason=kind,
                proof={"operation_id": operation_id, "evidence": evidence},
            )
            for row in db.execute("SELECT id FROM analysis_runs").fetchall():
                exclusions.apply_run(store, row[0])
    if kind == "reopened":
        exclusions.restore_owner(
            analysis_store(db), source, source_id, operation_id=operation_id, evidence=evidence
        )
    return dict(
        db.execute(
            "SELECT * FROM career_assistant_events WHERE operation_id=?", (operation_id,)
        ).fetchone()
    )


def shortlist(db, *, profile=None, limit=20, unseen=False):
    # Reuse the service's actual version/freshness/PASS rules, never re-score here.
    rows = recommendations(analysis_store(db), profile=profile, limit=10000)
    history = events(db)
    blocked, shown = set(), set()
    for event in history:
        identity = (event["source"], event["source_id"])
        if event["kind"] in ("dismissed", "applied", "withdrawn", "rejected"):
            blocked.add(identity)
        elif event["kind"] == "reopened":
            blocked.discard(identity)
        elif event["kind"] == "shown":
            shown.add(identity)
    excluded = blocked | shown if unseen else blocked
    picked = [
        r
        for r in rows
        if (r["source"], r["source_id"]) not in excluded
        and not exclusions.identity_blocked(db, r["source"], r["source_id"])
    ]
    return {"items": picked[:limit], "available": len(picked), "truncated": len(rows) == 10000}


def card(db, source, source_id):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='filter_decisions'").fetchone():
        decision = db.execute(
            "SELECT f.decision FROM filter_decisions f JOIN vacancies v "
            "ON v.source=f.source AND v.source_id=f.source_id AND v.content_hash=f.content_hash "
            "WHERE f.source=? AND f.source_id=? ORDER BY f.evaluated_at DESC,f.rowid DESC LIMIT 1",
            (source, source_id),
        ).fetchone()
        if decision and decision[0] == "FILTERED_OUT":
            return {"excluded": True, "source": source, "source_id": source_id}
    row = db.execute(
        """SELECT n.payload,v.detail_fetched_at,v.detail_status FROM normalized_snapshots n
      JOIN vacancies v ON v.source=n.source AND v.source_id=n.source_id
        AND v.content_hash=n.content_hash
      WHERE n.source=? AND n.source_id=? ORDER BY n.rowid DESC LIMIT 1""",
        (source, source_id),
    ).fetchone()
    if not row:
        raise ValueError("unknown_full_vacancy")
    if row["detail_status"] == "archived":
        return {"excluded": True, "source": source, "source_id": source_id}
    return {
        "vacancy": json.loads(row["payload"]),
        "detail_fetched_at": row["detail_fetched_at"],
        "detail_status": row["detail_status"],
        "live_platform_checked": False,
    }


def catalog(db, query, source=None, limit=20, offset=0, analysis=None):
    db.create_function("casefold", 1, lambda x: str(x or "").casefold(), deterministic=True)
    needle = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    sql = """FROM vacancies v JOIN normalized_snapshots n
      ON v.source=n.source AND v.source_id=n.source_id AND v.content_hash=n.content_hash
      WHERE n.rowid=(SELECT MAX(n2.rowid) FROM normalized_snapshots n2
        WHERE n2.source=n.source AND n2.source_id=n.source_id AND n2.content_hash=n.content_hash)
      AND COALESCE(json_extract(n.payload,'$.detail_status'),'full')!='archived'
      AND (casefold(json_extract(n.payload,'$.title')) LIKE casefold(?) ESCAPE '\\'
        OR casefold(json_extract(n.payload,'$.company')) LIKE casefold(?) ESCAPE '\\')"""
    if analysis is not None:
        db.create_function(
            "is_excluded", 2, lambda s, i: int(exclusions.identity_blocked(analysis, s, i))
        )
        sql += " AND is_excluded(v.source,v.source_id)=0"
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='filter_decisions'").fetchone():
        sql += (
            " AND COALESCE((SELECT f.decision FROM filter_decisions f "
            + "WHERE f.source=v.source AND f.source_id=v.source_id "
            + "AND f.content_hash=v.content_hash "
            + "ORDER BY f.evaluated_at DESC,f.rowid DESC LIMIT 1),'KEEP_UNKNOWN')!='FILTERED_OUT'"
        )
    args = [needle, needle]
    if source:
        sql += " AND v.source=?"
        args.append(source)
    total = db.execute("SELECT COUNT(*) " + sql, args).fetchone()[0]
    rows = db.execute(
        "SELECT n.payload,v.detail_fetched_at "
        + sql
        + " ORDER BY v.source,v.source_id LIMIT ? OFFSET ?",
        [*args, limit, offset],
    ).fetchall()
    items = []
    for row in rows:
        value = json.loads(row["payload"])
        items.append(
            {
                k: value.get(k)
                for k in ("source", "source_id", "title", "company", "url", "published_at")
            }
            | {"detail_fetched_at": row["detail_fetched_at"], "evaluated_fit": "not_asserted"}
        )
    return {
        "items": items,
        "total": total,
        "next_offset": offset + len(items) if offset + len(items) < total else None,
    }


def immutable_json(path, value):
    data = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".career-version-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            return json.loads(path.read_text())
        return value
    finally:
        os.unlink(tmp)


def stage_resume(config_path, profile, text_file, evidence):
    config_path = config_path.resolve()
    cfg = json.loads(config_path.read_text())
    base = (config_path.parent / cfg["profiles"][profile]).resolve()
    original = ResumeSnapshot.model_validate_json(base.read_text())
    if original.profile_id != profile:
        raise ValueError("configured_resume_profile_mismatch")
    text = text_file.read_text()
    if not evidence.strip() or not text.strip():
        raise ValueError("resume_text_and_readback_evidence_required")
    sha = text_hash(text)
    target = base.with_name(f"{profile}-career-{sha}.json")
    snapshot = original.model_dump(mode="json") | {
        "text": text,
        "content_hash": sha,
        "version": "career-" + sha,
        "captured_at": datetime.now(UTC).isoformat(),
        "provenance": original.provenance | {"owner_approved_readback": evidence},
    }
    saved = immutable_json(target, snapshot)
    valid = ResumeSnapshot.model_validate(saved)
    if valid.text != text or valid.profile_id != profile:
        raise ValueError("resume_version_conflict")
    cfg["profiles"][profile] = str(target)
    signature = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
    next_config = config_path.with_name(f"analysis-career-{signature}.json")
    if immutable_json(next_config, cfg) != cfg:
        raise ValueError("next_config_conflict")
    return {
        "profile": profile,
        "resume": str(target),
        "content_hash": sha,
        "next_config": str(next_config),
        "active_config_changed": False,
        "analysis_started": False,
    }


def compact_event(row):
    result = dict(row)
    result.pop("fingerprint", None)
    if row["kind"] != "legacy_observation":
        return result
    payload = json.loads(row["detail"])
    original = payload.get("original", {})
    props = original.get("properties", {})
    result["detail"] = {
        "historical": True,
        "not_reverified_live": True,
        "original_status": original.get("source_status", original.get("kind")),
        "original_date": original.get("event_at", props.get("date:Дата ответа:start")),
        "next_step": props.get("Следующий шаг"),
        "needs_action": props.get("Нужно действие"),
        "reply": str(props.get("Последний ответ", ""))[:500],
        "application_date": props.get("date:Дата отклика:start"),
        "deadline": props.get("date:Срок:start"),
    }
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--collector", type=Path, required=True)
    p.add_argument("--analysis", type=Path, required=True)
    p.add_argument("--output", type=Path)
    p.add_argument("--output-root", type=Path, help="Explicit allowed directory for file export")
    p.add_argument("--config", type=Path)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("shortlist")
    s.add_argument(
        "--profile", choices=("product", "automation", "business_assistant", "business_analyst")
    )
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--unseen", action="store_true")
    c = sub.add_parser("catalog")
    c.add_argument("--query", default="")
    c.add_argument("--source", choices=SOURCES)
    c.add_argument("--limit", type=int, default=20)
    c.add_argument("--offset", type=int, default=0)
    for name in ("card", "history", "record"):
        c = sub.add_parser(name)
        c.add_argument("--source", choices=SOURCES, required=name != "history")
        c.add_argument("--id", dest="source_id", required=name != "history")
        if name == "history":
            c.add_argument("--limit", type=int, default=50)
            c.add_argument("--offset", type=int, default=0)
            c.add_argument("--full", action="store_true")
        if name == "record":
            c.add_argument("--operation-id", required=True)
            c.add_argument("--kind", choices=KINDS, required=True)
            c.add_argument("--evidence", required=True)
            c.add_argument("--detail", default="")
    sub.add_parser("status")
    r = sub.add_parser("resume")
    r.add_argument(
        "--profile",
        choices=("product", "automation", "business_assistant", "business_analyst"),
        required=True,
    )
    r = sub.add_parser("stage-resume")
    r.add_argument(
        "--profile",
        choices=("product", "automation", "business_assistant", "business_analyst"),
        required=True,
    )
    r.add_argument("--text-file", type=Path, required=True)
    r.add_argument("--evidence", required=True)
    a = p.parse_args()
    if not 1 <= getattr(a, "limit", 20) <= 200 or getattr(a, "offset", 0) < 0:
        p.error("limit 1..200 and nonnegative offset required")
    # File export is opt-in and constrained by the caller's explicit directory.
    if a.output:
        if a.output_root is None or not a.output_root.is_dir():
            p.error("output requires an existing --output-root directory")
        if not a.output.resolve().is_relative_to(a.output_root.resolve()):
            p.error("output must be inside --output-root")
        if a.output.exists():
            p.error("output exists; read it to reconcile before retrying")
    if a.command == "stage-resume":
        result = stage_resume(a.config, a.profile, a.text_file, a.evidence)
    elif a.command == "resume":
        cfg = json.loads(a.config.read_text())
        rp = (a.config.parent / cfg["profiles"][a.profile]).resolve()
        result = json.loads(rp.read_text())
    elif a.command in ("card", "catalog"):
        with closing(connect(a.analysis)) as analysis, closing(connect(a.collector)) as db:
            analysis_store(analysis)
            if a.command == "card" and exclusions.identity_blocked(analysis, a.source, a.source_id):
                result = {"excluded": True, "source": a.source, "source_id": a.source_id}
            else:
                result = (
                    card(db, a.source, a.source_id)
                    if a.command == "card"
                    else catalog(db, a.query, a.source, a.limit, a.offset, analysis=analysis)
                )
    else:
        with closing(connect(a.analysis, write=a.command == "record")) as db:
            analysis_store(db)
            if a.command == "record":
                with closing(connect(a.collector)) as producer:
                    try:
                        card(producer, a.source, a.source_id)
                    except ValueError:
                        if not events(db, a.source, a.source_id):
                            raise
                result = record(
                    db, a.operation_id, a.source, a.source_id, a.kind, a.evidence, a.detail
                )
            elif a.command == "history":
                all_events = events(db, a.source, a.source_id)
                selected = all_events[a.offset : a.offset + a.limit]
                current_config = json.loads(a.config.read_text()) if a.config else {}
                versions = {}
                for profile, relative in current_config.get("profiles", {}).items():
                    cv = json.loads((a.config.parent / relative).read_text())
                    versions[profile] = {
                        key: cv[key]
                        for key in (
                            "resume_id",
                            "source_url",
                            "version",
                            "captured_at",
                            "content_hash",
                        )
                    }
                result = {
                    "events": selected if a.full else [compact_event(e) for e in selected],
                    "total": len(all_events),
                    "next_offset": a.offset + len(selected)
                    if a.offset + len(selected) < len(all_events)
                    else None,
                }
            elif a.command == "shortlist":
                result = shortlist(db, profile=a.profile, limit=a.limit, unseen=a.unseen)
            else:
                row = db.execute(
                    "SELECT id,created_at,config_json FROM analysis_runs "
                    "ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                cfg = json.loads(row["config_json"]) if row else {}
                result = {
                    "latest_run": row["id"] if row else None,
                    "counts": dict(
                        db.execute(
                            "SELECT status,COUNT(*) FROM run_jobs WHERE run_id=? GROUP BY status",
                            (row["id"],),
                        )
                    )
                    if row
                    else {},
                    "current_service_resume_versions": versions,
                    "evaluation_policy": cfg.get("evaluation_policy"),
                    "model_calls_started": 0,
                }
    data = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with a.output.open("x", encoding="utf-8") as f:
            os.chmod(a.output, 0o600)
            f.write(data)
        print(json.dumps({"ok": True, "output": str(a.output)}))
    else:
        print(data)


if __name__ == "__main__":
    main()
