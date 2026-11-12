"""Manually started append-only campaigns. Durable discovery and first acquisitions."""

import json
import time
import uuid
from datetime import UTC, datetime

from career_scout.common import SourceError, utcnow
from career_scout.filtering import evaluate
from career_scout.normalization import NORMALIZER
from career_scout.search_config import ENGINE_VERSION, FilterPolicy, plan

SOURCE_ERRORS = {
    "auth_required",
    "auth_transport_error",
    "access_denied",
    "rate_limited",
    "missing_application_credentials",
    "missing_user_agent",
    "source_storage_policy_unconfirmed",
}


class Campaigns:
    def __init__(self, store):
        self.store = store
        self.db = store.db
        self._age_plans = {}

    def resolve(self, identifier):
        row = self.db.execute("SELECT id FROM campaigns WHERE id=?", (identifier,)).fetchone()
        if row:
            return row[0]
        row = self.db.execute(
            "SELECT campaign_id FROM campaign_runs WHERE run_id=?", (identifier,)
        ).fetchone()
        return row[0] if row else None

    def row(self, cid):
        row = self.db.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
        if not row:
            raise SourceError("unknown_campaign")
        return row

    def create(
        self,
        config,
        mode,
        *,
        sources=None,
        queries=None,
        per_page=None,
        runtime=None,
        lookback_days=14,
    ):
        if mode not in {"initial", "new"}:
            raise SourceError("invalid_campaign_mode")
        names = (
            sources
            if sources is not None
            else [
                s
                for s in ("hh", "trudvsem", "habr", "superjob")
                if s in config.sources and config.sources[s].enabled
            ]
        )
        if not names:
            raise SourceError("no_enabled_sources")
        now = datetime.now(UTC).replace(microsecond=0)
        plans = []
        for source in names:
            limited = lookback_days is not None
            frozen = plan(
                config,
                source=source,
                queries=queries,
                per_page=per_page,
                days=lookback_days if limited else None,
                now=now,
            )
            frozen.update(
                start=frozen["start"] if limited else "",
                append_only=True,
                collection_mode=mode,
                publication_start=frozen["start"] if limited else None,
                publication_end=frozen["end"] if limited else None,
                lookback_days=lookback_days if limited else None,
            )
            frozen["window_field"] = "all_visible" if source == "habr" else frozen["window_field"]
            if runtime:
                frozen["runtime"].update(runtime)
            plans.append(frozen)
        cid = str(uuid.uuid4())
        with self.store.lock(), self.db:
            self.db.execute(
                "INSERT INTO campaigns(id,mode,created_at,plan_json) VALUES(?,?,?,?)",
                (cid, mode, utcnow(), json.dumps({"plans": plans}, ensure_ascii=False)),
            )
            for frozen in plans:
                source = frozen["source"]
                rid = self.store.create(
                    frozen["queries"],
                    frozen["start"],
                    frozen["end"],
                    frozen["per_page"],
                    frozen["ttl_hours"],
                    source=source,
                    plan_snapshot=frozen,
                    commit=False,
                )
                self.db.execute("INSERT INTO campaign_runs VALUES(?,?,?)", (cid, source, rid))
                self.db.execute(
                    "INSERT INTO campaign_items SELECT ?,source,source_id,'backlog' "
                    "FROM vacancies WHERE source=? AND content_hash IS NULL "
                    "AND detail_status IN ('missing','stale','partial')",
                    (cid, source),
                )
        return cid

    def age(self, cid, source, listing, published=None):
        """Hard publication window, separate from relevance and source modification time."""
        key = (cid, source)
        if key not in self._age_plans:
            self._age_plans[key] = next(
                p for p in json.loads(self.row(cid)["plan_json"])["plans"] if p["source"] == source
            )
        frozen = self._age_plans[key]
        lower = frozen.get("publication_start")
        if not lower:
            return "unrestricted"
        if published is None:
            raw = json.loads(listing) if isinstance(listing, str) else listing
            published = {
                "hh": raw.get("published_at"),
                "superjob": raw.get("date_published"),
                "trudvsem": raw.get("creation-date"),
                "habr": (raw.get("publishedDate") or {}).get("date")
                if isinstance(raw.get("publishedDate"), dict)
                else None,
            }[source]
        start = datetime.fromisoformat(lower)
        end = datetime.fromisoformat(frozen["publication_end"])
        try:
            if type(published) in (int, float):
                date = datetime.fromtimestamp(published, UTC)
            elif isinstance(published, str) and len(published) == 10:
                day = datetime.fromisoformat(published).date()
                if day < start.date() or day > end.date():
                    return "outside"
                # A date without a time cannot prove the lower boundary hour.
                return "unknown" if day == start.date() else "recent"
            else:
                date = datetime.fromisoformat(published)
                if date.tzinfo is None:
                    return "unknown"
        except (ValueError, TypeError, OverflowError, OSError):
            return "unknown"
        return "recent" if start <= date <= end else "outside"

    def runs(self, cid):
        self.row(cid)
        return self.db.execute(
            "SELECT r.* FROM runs r JOIN campaign_runs c ON c.run_id=r.id "
            "WHERE c.campaign_id=? ORDER BY r.rowid",
            (cid,),
        ).fetchall()

    def _counts(self, cid, source):
        rows = self.db.execute(
            "SELECT i.origin,v.detail_status,v.content_hash,v.listing_json,"
            "json_extract(n.payload,'$.published_at') published FROM campaign_items i "
            "JOIN vacancies v ON v.source=i.source AND v.source_id=i.source_id "
            "LEFT JOIN normalized_snapshots n ON n.source=v.source AND n.source_id=v.source_id "
            "AND n.content_hash=v.content_hash AND n.normalizer_version=? "
            "WHERE i.campaign_id=? AND i.source=?",
            (NORMALIZER, cid, source),
        ).fetchall()
        counts = {
            "discovered_ids": 0,
            "new_ids": 0,
            "known_ids": 0,
            "backlog_ids": 0,
            "saved_descriptions": 0,
            "unavailable": 0,
            "pending_details": 0,
            "outside_publication_window": 0,
            "unknown_publication_date": 0,
        }
        for row in rows:
            n = 1
            age = self.age(cid, source, row["listing_json"], row["published"])
            counts["outside_publication_window"] += age == "outside"
            counts["unknown_publication_date"] += age == "unknown"
            counts["discovered_ids"] += n
            counts[
                {"new": "new_ids", "known": "known_ids", "backlog": "backlog_ids"}[row["origin"]]
            ] += n
            if row["content_hash"] is not None:
                counts["saved_descriptions"] += n
            elif row["detail_status"] == "unavailable":
                counts["unavailable"] += n
            elif age != "outside":
                counts["pending_details"] += n
        counts["failed_details"] = self.db.execute(
            "SELECT COUNT(*) FROM campaign_items i JOIN detail_failures f ON f.source=i.source "
            "AND f.source_id=i.source_id JOIN vacancies v ON v.source=i.source AND "
            "v.source_id=i.source_id "
            "WHERE i.campaign_id=? AND i.source=? AND f.state='failed' AND v.content_hash IS NULL",
            (cid, source),
        ).fetchone()[0]
        counts["new_descriptions"] = self.db.execute(
            "SELECT COUNT(*) FROM acquisitions WHERE campaign_id=? AND source=?", (cid, source)
        ).fetchone()[0]
        return counts

    def source_report(self, cid, run):
        if isinstance(run, str):
            run = self.store.run(run)
        source = run["source"]
        rid = run["id"]
        counts = self._counts(cid, source)
        qs = self.db.execute(
            "SELECT status,truncated,COUNT(*) n FROM queries WHERE run_id=? "
            "GROUP BY status,truncated",
            (rid,),
        ).fetchall()
        pending = sum(q["n"] for q in qs if q["status"] == "pending")
        truncated = sum(q["n"] for q in qs if q["truncated"])
        complete = not (pending or truncated or counts["pending_details"] or run["rejected"])
        status = "complete" if complete else "partial"
        if not complete and run["error"] in SOURCE_ERRORS:
            status = "blocked"
        policy = self.store.active_policy(rid)
        filters = {}
        if policy:
            filters = dict(
                self.db.execute(
                    "SELECT f.decision,COUNT(*) FROM campaign_items i JOIN vacancies v "
                    "ON v.source=i.source AND v.source_id=i.source_id JOIN filter_decisions f "
                    "ON f.source=v.source AND f.source_id=v.source_id AND "
                    "f.content_hash=v.content_hash "
                    "WHERE i.campaign_id=? AND i.source=? AND f.policy_hash=? "
                    "AND f.normalizer_version=? GROUP BY f.decision",
                    (cid, source, policy["policy_hash"], NORMALIZER),
                )
            )
        return {
            "run_id": rid,
            "source": source,
            "status": status,
            "error": run["error"],
            "requests": run["requests"],
            "pages_saved": run["pages_saved"],
            "pending_queries": pending,
            "truncated_queries": truncated,
            "rejected_rows": run["rejected"],
            "discovery_complete": not (pending or truncated or run["rejected"]),
            "pagination_replans": self.db.execute(
                "SELECT COUNT(*) FROM events WHERE run_id=? AND kind='pagination_replan'",
                (rid,),
            ).fetchone()[0],
            **counts,
            "filtering": filters,
        }

    def report(self, cid):
        campaign = self.row(cid)
        sources = [self.source_report(cid, r) for r in self.runs(cid)]
        complete = all(s["status"] == "complete" for s in sources)
        return {
            "campaign_id": cid,
            "mode": campaign["mode"],
            "created_at": campaign["created_at"],
            "publication_window": [
                {
                    "source": p["source"],
                    "from": p.get("publication_start"),
                    "to": p.get("publication_end"),
                }
                for p in json.loads(campaign["plan_json"])["plans"]
            ],
            "status": "complete" if complete else "partial",
            "sources": sources,
            "totals": {
                key: sum(s[key] for s in sources)
                for key in (
                    "requests",
                    "pages_saved",
                    "discovered_ids",
                    "new_ids",
                    "known_ids",
                    "backlog_ids",
                    "new_descriptions",
                    "saved_descriptions",
                    "pending_details",
                    "failed_details",
                    "unavailable",
                    "outside_publication_window",
                    "unknown_publication_date",
                )
            },
        }

    def pending(self, cid, source, clock):
        rows = self.db.execute(
            "SELECT v.source_id,v.listing_json FROM campaign_items i JOIN vacancies v "
            "ON v.source=i.source AND v.source_id=i.source_id LEFT JOIN detail_failures f "
            "ON f.source=v.source AND f.source_id=v.source_id WHERE i.campaign_id=? AND i.source=? "
            "AND v.content_hash IS NULL AND v.detail_status IN ('missing','stale','partial') "
            "AND (f.source IS NULL OR (f.state='pending' AND f.retry_at<=?)) "
            "ORDER BY v.source_id",
            (cid, source, clock),
        )
        return next(
            (r for r in rows if self.age(cid, source, r["listing_json"]) != "outside"), None
        )

    def fail_detail(self, source, vid, error, clock):
        old = self.db.execute(
            "SELECT attempts FROM detail_failures WHERE source=? AND source_id=?", (source, vid)
        ).fetchone()
        attempts = (old[0] if old else 0) + 1
        with self.db:
            self.db.execute(
                "INSERT INTO detail_failures VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(source,source_id) DO UPDATE SET attempts=excluded.attempts, "
                "retry_at=excluded.retry_at,state=excluded.state,error=excluded.error",
                (
                    source,
                    vid,
                    attempts,
                    clock + min(60 * attempts, 300),
                    "failed" if attempts >= 3 else "pending",
                    error,
                ),
            )

    def filter(self, cid, rid):
        policy_row = self.store.active_policy(rid)
        if not policy_row:
            return
        if policy_row["engine_version"] != ENGINE_VERSION:
            raise SourceError("filter_engine_version_unavailable")
        policy = FilterPolicy.model_validate_json(policy_row["policy_json"])
        source = self.store.run(rid)["source"]
        pending = self.db.execute(
            "SELECT n.payload FROM campaign_items i JOIN vacancies v ON v.source=i.source "
            "AND v.source_id=i.source_id JOIN normalized_snapshots n ON n.source=v.source "
            "AND n.source_id=v.source_id AND n.content_hash=v.content_hash "
            "WHERE i.campaign_id=? AND i.source=? AND n.normalizer_version=? "
            "AND json_extract(n.payload,'$.detail_status')='full' AND NOT EXISTS "
            "(SELECT 1 FROM filter_decisions f WHERE f.source=n.source AND f.source_id=n.source_id "
            "AND f.content_hash=n.content_hash AND f.normalizer_version=n.normalizer_version "
            "AND f.policy_hash=?)",
            (cid, source, NORMALIZER, policy.key()),
        ).fetchall()
        with self.db:
            for row in pending:
                vacancy = json.loads(row[0])
                result = evaluate(vacancy, policy)
                self.db.execute(
                    "INSERT OR IGNORE INTO filter_decisions VALUES(?,?,?,?,?,?,?,?)",
                    (
                        source,
                        vacancy["source_id"],
                        vacancy["content_hash"],
                        NORMALIZER,
                        policy.key(),
                        result.status,
                        json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
                        utcnow(),
                    ),
                )

    def chunk(self, cid, rid, client, *, max_details=200, progress=None):
        source = self.store.run(rid)["source"]
        error = None
        details = 0
        with self.store.lock():
            cooldown = self.store.cooldown(source)
            if cooldown and cooldown > client.clock():
                return self.source_report(cid, rid)
            self.store.begin(rid)
            frozen = json.loads(self.store.run(rid)["plan_json"])

            def reserve():
                self.store.reserve_request(rid)
                if progress:
                    progress(
                        {
                            "event": "request",
                            "campaign_id": cid,
                            "source": source,
                            "http_attempt": client.used + 1,
                            "at": utcnow(),
                        }
                    )

            client.on_request = reserve
            try:
                if source != getattr(client, "source", source):
                    raise SourceError("source_adapter_mismatch")
                if source == "superjob" and not frozen["source_options"].get("storage_allowed"):
                    raise SourceError("source_storage_policy_unconfirmed")
                while True:
                    pending = self.pending(cid, source, client.clock())
                    if pending and details < max_details:
                        vid = pending["source_id"]
                        try:
                            snapshot = client.detail(vid)
                            if snapshot["source"] != source or snapshot["source_id"] != vid:
                                raise SourceError("detail_identity_mismatch")
                        except SourceError as exc:
                            if exc.code == "request_budget_exhausted" or exc.code in SOURCE_ERRORS:
                                raise
                            if exc.code == "unavailable":
                                self.store.unavailable(vid, source)
                            else:
                                self.fail_detail(source, vid, exc.code, client.clock())
                        else:
                            self.store.save_detail(snapshot, campaign_id=cid, run_id=rid)
                        details += 1
                        continue
                    query = self.store.next_query(rid)
                    if query is None:
                        break
                    page_size = self.store.page_size(query["id"], frozen["per_page"])
                    native_max = getattr(client, "max_page_size", None)
                    if native_max is not None:
                        page_size = min(page_size, native_max)
                    try:
                        page = client.search(
                            query["query"],
                            query["start"] or None,
                            query["end"],
                            query["next_page"],
                            page_size,
                            area_ids=frozen.get("source_options", {}).get("area_ids", []),
                        )
                    except SourceError as exc:
                        # Never translate a live cursor to a different stride.
                        # Re-read the same query from page zero at a smaller size;
                        # committed descriptions are still reused by identity.
                        if (
                            exc.code in {"transport_error", "server_error"}
                            and getattr(client, "replan_slow_pages", False)
                            and page_size > 1
                        ):
                            new_query = self.store.replan_page_size(query, page_size, exc.code)
                            if progress:
                                progress(
                                    {
                                        "event": "page_size_replan",
                                        "source": source,
                                        "campaign_id": cid,
                                        "query_id": query["id"],
                                        "new_query_id": new_query,
                                        "page_size": page_size // 2,
                                        "reason": exc.code,
                                    }
                                )
                            continue
                        raise
                    cap = getattr(client, "window_cap", None)
                    if (
                        cap
                        and ((page.found > cap and query["next_page"] == 0) or page.truncated)
                        and getattr(client, "supports_date_split", False)
                    ):
                        if self.store.split(query, page.found):
                            continue
                    self.store.save_page(query, page, frozen["ttl_hours"], campaign_id=cid)
                    inline = getattr(client, "inline_detail", None)
                    if inline:
                        for item in page.items:
                            old = self.db.execute(
                                "SELECT content_hash,detail_status FROM vacancies "
                                "WHERE source=? AND source_id=?",
                                (source, item["id"]),
                            ).fetchone()
                            if (
                                old
                                and old[0] is None
                                and old[1] != "unavailable"
                                and self.age(cid, source, item) != "outside"
                            ):
                                snapshot = inline(item)
                                if snapshot:
                                    self.store.save_detail(snapshot, campaign_id=cid, run_id=rid)
            except SourceError as exc:
                error = exc
            except KeyboardInterrupt:
                error = SourceError("interrupted")
            self.filter(cid, rid)
            with self.db:
                self.db.execute(
                    "UPDATE runs SET error=?,cooldown_until=? WHERE id=?",
                    (error.code if error else None, error.retry_at if error else None, rid),
                )
                if error:
                    self.store.event(rid, "source_error", error.code)
                    if error.retry_at:
                        self.db.execute(
                            "UPDATE source_state SET cooldown_until=? WHERE id=?",
                            (error.retry_at, source),
                        )
                status = self.source_report(cid, rid)["status"]
                self.db.execute("UPDATE runs SET status=? WHERE id=?", (status, rid))
            return self.source_report(cid, rid)

    def drive(
        self,
        cid,
        factory,
        *,
        chunk_requests=200,
        max_rounds=None,
        progress=None,
        sleep=time.sleep,
        clock=time.time,
        retry_failed=False,
    ):
        self.row(cid)
        # A manual resume may follow a corrected credential/configuration problem.
        # Within this invocation permanent source errors stop only that source.
        with self.store.lock(), self.db:
            self.db.execute(
                "UPDATE runs SET error=NULL WHERE id IN "
                "(SELECT run_id FROM campaign_runs WHERE campaign_id=?) "
                "AND error IN ('auth_required','auth_transport_error','access_denied',"
                "'missing_application_credentials','missing_user_agent')",
                (cid,),
            )
            if retry_failed:
                self.db.execute(
                    "UPDATE detail_failures SET attempts=0,state='pending',retry_at=0 "
                    "WHERE EXISTS(SELECT 1 FROM campaign_items i WHERE i.campaign_id=? "
                    "AND i.source=detail_failures.source AND "
                    "i.source_id=detail_failures.source_id)",
                    (cid,),
                )
        rounds = 0
        last_requests = {}
        stopped_sources = set()
        rate_limits = {}
        while max_rounds is None or rounds < max_rounds:
            did_work = False
            wakeups = []
            interrupted = False
            for run in self.runs(cid):
                rid = run["id"]
                source = run["source"]
                if source in stopped_sources:
                    continue
                before = self.source_report(cid, run)
                if before["status"] == "complete":
                    continue
                if before["error"] in SOURCE_ERRORS and before["error"] != "rate_limited":
                    continue
                cooldown = self.store.cooldown(source)
                if cooldown and cooldown > clock():
                    wakeups.append(cooldown)
                    continue
                if not before["pending_queries"] and not self.pending(cid, source, clock()):
                    future = self.db.execute(
                        "SELECT MIN(f.retry_at) FROM detail_failures f JOIN campaign_items i "
                        "ON i.source=f.source AND i.source_id=f.source_id WHERE i.campaign_id=? "
                        "AND i.source=? AND f.state='pending'",
                        (cid, source),
                    ).fetchone()[0]
                    if future and future > clock():
                        wakeups.append(future)
                    continue
                frozen = json.loads(run["plan_json"])
                client = None
                try:
                    client = factory(frozen, chunk_requests)
                    client.last_request = last_requests.get(source)
                    result = self.chunk(
                        cid,
                        rid,
                        client,
                        max_details=chunk_requests,
                        progress=progress,
                    )
                except SourceError as exc:
                    with self.store.lock(), self.db:
                        self.db.execute(
                            "UPDATE runs SET error=?,status=? WHERE id=?",
                            (exc.code, "blocked", rid),
                        )
                    result = self.source_report(cid, rid)
                finally:
                    if client:
                        last_requests[source] = client.last_request
                        client.close()
                did_work = did_work or result["requests"] > before["requests"]
                if progress:
                    progress({"event": "chunk", "campaign_id": cid, **result})
                if result["error"] not in {None, "request_budget_exhausted", "rate_limited"}:
                    stopped_sources.add(source)
                if result["error"] == "rate_limited":
                    rate_limits[source] = rate_limits.get(source, 0) + 1
                    if rate_limits[source] >= 3:
                        stopped_sources.add(source)
                if result["error"] == "interrupted":
                    interrupted = True
                    break
            rounds += 1
            report = self.report(cid)
            with self.db:
                self.db.execute("UPDATE campaigns SET status=? WHERE id=?", (report["status"], cid))
            if report["status"] == "complete" or interrupted:
                return report
            if not did_work:
                if wakeups:
                    delay = max(0, min(wakeups) - clock())
                    if progress:
                        progress({"event": "waiting", "campaign_id": cid, "seconds": delay})
                    sleep(min(delay, 30))
                else:
                    return report
        return self.report(cid)

    def export(self, cid, *, include_excluded=False, include_known=None):
        self.row(cid)
        if include_known is None:
            include_known = self.row(cid)["mode"] == "initial"
        self.db.execute("BEGIN")
        try:
            result = []
            decisions = []
            undecided = 0
            for run in self.runs(cid):
                policy = self.store.active_policy(run["id"])
                if not policy:
                    raise SourceError("campaign_filter_policy_missing")
                rows = self.db.execute(
                    "SELECT n.payload,v.listing_json,f.payload decision "
                    "FROM campaign_items i JOIN vacancies v "
                    "ON v.source=i.source AND v.source_id=i.source_id JOIN normalized_snapshots n "
                    "ON n.source=v.source AND n.source_id=v.source_id AND "
                    "n.content_hash=v.content_hash "
                    "LEFT JOIN filter_decisions f ON f.source=n.source AND f.source_id=n.source_id "
                    "AND f.content_hash=n.content_hash AND "
                    "f.normalizer_version=n.normalizer_version "
                    "AND f.policy_hash=? WHERE i.campaign_id=? AND i.source=? "
                    "AND n.normalizer_version=? AND "
                    "json_extract(n.payload,'$.detail_status')='full' "
                    "AND (? OR EXISTS(SELECT 1 FROM acquisitions a WHERE a.source=i.source "
                    "AND a.source_id=i.source_id AND a.campaign_id=i.campaign_id)) "
                    "ORDER BY n.source_id",
                    (policy["policy_hash"], cid, run["source"], NORMALIZER, include_known),
                ).fetchall()
                for row in rows:
                    vacancy = json.loads(row["payload"])
                    if self.age(
                        cid, run["source"], row["listing_json"], vacancy.get("published_at")
                    ) not in {
                        "recent",
                        "unrestricted",
                    }:
                        continue
                    if row["decision"] is None:
                        undecided += 1
                        if not include_excluded:
                            continue
                    else:
                        decision = json.loads(row["decision"])
                        vacancy = json.loads(row["payload"])
                        decisions.append(
                            {
                                "source": vacancy["source"],
                                "source_id": vacancy["source_id"],
                                "content_hash": vacancy["content_hash"],
                                "decision": decision,
                            }
                        )
                        if decision["status"] == "FILTERED_OUT" and not include_excluded:
                            continue
                    result.append(json.loads(row["payload"]))
            return {
                "schema_version": 2,
                "normalizer_version": NORMALIZER,
                "campaign_id": cid,
                "mode": self.row(cid)["mode"],
                "selection": {
                    "scope": "catalog" if include_known else "new",
                    "include_excluded": include_excluded,
                    "undecided": undecided,
                },
                "report": self.report(cid),
                "filter_decisions": decisions,
                "vacancies": result,
            }
        finally:
            self.db.rollback()
