"""Synchronous, restartable source collection. No model or agent dependencies."""

import json

from career_scout.common import SourceError
from career_scout.source_types import SourceClient
from career_scout.storage import Store


def collect(store: Store, client: SourceClient, run_id: str, *, max_details: int = 30) -> dict:
    with store.lock():
        run = store.run(run_id)
        cooldown = store.cooldown(run["source"])
        if cooldown and cooldown > client.clock():
            return store.finish(run_id, SourceError("rate_limited", cooldown))
        store.begin(run_id)
        client.on_request = lambda: store.reserve_request(run_id)
        plan = json.loads(run["plan_json"])
        error = None
        details = 0
        try:
            if run["source"] != getattr(client, "source", "hh"):
                raise SourceError("source_adapter_mismatch")
            if run["source"] == "superjob" and not plan.get("source_options", {}).get(
                "storage_allowed"
            ):
                raise SourceError("source_storage_policy_unconfirmed")
            while True:
                # Drain existing pending detail before fetching the next page. Its state survives
                # a crash, so a committed page is never skipped just because its details failed.
                pending = store.pending_detail(run_id)
                if pending and details < max_details:
                    try:
                        snapshot = client.detail(pending["source_id"])
                    except SourceError as exc:
                        if exc.code != "unavailable":
                            raise
                        store.unavailable(pending["source_id"], pending["source"])
                    else:
                        store.save_detail(snapshot)
                    details += 1
                    continue
                query = store.next_query(run_id)
                if query is None:
                    break
                page = client.search(
                    query["query"],
                    query["start"],
                    query["end"],
                    query["next_page"],
                    store.page_size(query["id"], plan["per_page"]),
                    area_ids=plan.get("source_options", {}).get("area_ids", []),
                )
                cap = getattr(client, "window_cap", 2000)
                if (
                    cap is not None
                    and page.found > cap
                    and query["next_page"] == 0
                    and getattr(client, "supports_date_split", True)
                    and store.split(query, page.found)
                ):
                    continue
                store.save_page(query, page, plan["ttl_hours"])
        except SourceError as exc:
            error = exc
        except KeyboardInterrupt:
            error = SourceError("interrupted")
        store.filter_run(run_id)
        return store.finish(run_id, error)
